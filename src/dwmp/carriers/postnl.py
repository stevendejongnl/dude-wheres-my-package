import re
from datetime import UTC, datetime, timedelta

import httpx

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
    no_date_fallback,
)

# PostNL API endpoints
POSTNL_GRAPHQL_URL = "https://jouw.postnl.nl/account/api/graphql"
POSTNL_TRACK_URL = "https://jouw.postnl.nl/track-and-trace"

SHIPMENTS_QUERY = """
{
  trackedShipments {
    receiverShipments {
      ...parcelShipment
    }
    senderShipments {
      ...parcelShipment
    }
  }
}

fragment parcelShipment on TrackedShipmentResultType {
  key
  barcode
  title
  delivered
  deliveredTimeStamp
  deliveryWindowFrom
  deliveryWindowTo
  shipmentType
  detailsUrl
  creationDateTime
}
"""

# detailsUrl from the GraphQL response looks like
#   https://jouw.postnl.nl/track-and-trace/{barcode}/{postal_code}/{country}[/...]
# We mine the postal code from it so the unified refresh loop can still call
# public track() once the parcel drops off the authenticated account list.
_DETAILS_URL_POSTAL_CODE_RE = re.compile(
    r"/track-and-trace/[^/]+/([A-Za-z0-9]{4,10})/[A-Z]{2}"
)


def _postal_code_from_details_url(details_url: str | None) -> str | None:
    if not details_url:
        return None
    match = _DETAILS_URL_POSTAL_CODE_RE.search(details_url)
    return match.group(1) if match else None


# Ordered most-specific first to avoid partial matches
STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    ("Bezorger is onderweg", TrackingStatus.OUT_FOR_DELIVERY),
    ("Bezorgd", TrackingStatus.DELIVERED),
    ("Onderweg naar bestemming", TrackingStatus.IN_TRANSIT),
    ("Onderweg", TrackingStatus.IN_TRANSIT),
    ("In ontvangst genomen", TrackingStatus.IN_TRANSIT),
    ("Gesorteerd", TrackingStatus.IN_TRANSIT),
    ("Niet bezorgd", TrackingStatus.FAILED_ATTEMPT),
    ("Retour", TrackingStatus.RETURNED),
]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_status(text: str) -> TrackingStatus:
    for key, status in STATUS_MAP:
        if key.lower() in text.lower():
            return status
    return TrackingStatus.UNKNOWN


class PostNL(CarrierBase):
    name = "postnl"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        async with self._get_client() as client:
            response = await client.post(
                POSTNL_GRAPHQL_URL,
                json={"variables": {}, "query": SHIPMENTS_QUERY},
                headers={
                    "Authorization": f"Bearer {tokens.access_token}",
                    "Accept": "application/json",
                    "Accept-Language": "nl-NL",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()

        results: list[TrackingResult] = []
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        tracked = data.get("data", {}).get("trackedShipments", {})
        all_shipments = tracked.get("receiverShipments", []) + tracked.get("senderShipments", [])

        for shipment in all_shipments:
            result = self._parse_graphql_shipment(shipment)
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest < cutoff:
                    continue
            results.append(result)

        return results

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        postal_code = kwargs.get("postal_code", "")
        country = kwargs.get("country", "NL")
        url = f"{POSTNL_TRACK_URL}/{tracking_number}/{postal_code}/{country}"

        async with self._get_client() as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json, text/html",
                },
                follow_redirects=True,
            )
            response.raise_for_status()

        return self._parse_response(tracking_number, response)

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_response(
        self, tracking_number: str, response: httpx.Response
    ) -> TrackingResult:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return self._parse_json(tracking_number, response.json())
        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=TrackingStatus.UNKNOWN,
        )

    def _parse_graphql_shipment(self, shipment: dict) -> TrackingResult:
        barcode = shipment.get("barcode", shipment.get("key", "unknown"))
        delivered = shipment.get("delivered", False)
        status = TrackingStatus.DELIVERED if delivered else TrackingStatus.IN_TRANSIT
        title = shipment.get("title", "")

        events: list[TrackingEvent] = []

        created = shipment.get("creationDateTime")
        if created:
            try:
                events.append(TrackingEvent(
                    timestamp=_ensure_utc(datetime.fromisoformat(created)),
                    status=TrackingStatus.PRE_TRANSIT,
                    description=title or "Shipment registered",
                ))
            except ValueError:
                pass

        delivered_ts = shipment.get("deliveredTimeStamp")
        if delivered_ts:
            try:
                events.append(TrackingEvent(
                    timestamp=_ensure_utc(datetime.fromisoformat(delivered_ts)),
                    status=TrackingStatus.DELIVERED,
                    description="Bezorgd",
                ))
            except ValueError:
                pass

        estimated = None
        window_from = shipment.get("deliveryWindowFrom")
        if window_from:
            try:
                estimated = datetime.fromisoformat(window_from)
            except ValueError:
                pass

        return TrackingResult(
            tracking_number=barcode,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=sorted(events, key=lambda e: e.timestamp),
            postal_code=_postal_code_from_details_url(shipment.get("detailsUrl")),
        )

    def _parse_json(self, tracking_number: str, data: dict) -> TrackingResult:
        events: list[TrackingEvent] = []
        status = TrackingStatus.UNKNOWN

        colli = data if isinstance(data, dict) else {}
        if "colli" in colli:
            colli = colli["colli"]
            if isinstance(colli, list) and colli:
                colli = colli[0]

        status_text = colli.get("statusPhase", {}).get("message", "")
        if status_text:
            status = _parse_status(status_text)

        for event_data in colli.get("events", []):
            ts_str = event_data.get("dateTime") or event_data.get("timestamp", "")
            description = event_data.get("description", "")
            location = event_data.get("location", {})
            location_str = location.get("name") if isinstance(location, dict) else None

            ts = datetime.fromisoformat(ts_str) if ts_str else no_date_fallback()

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(description),
                    description=description,
                    location=location_str,
                )
            )

        estimated = colli.get("expectedDeliveryDate")

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            estimated_delivery=datetime.fromisoformat(estimated) if estimated else None,
            events=sorted(events, key=lambda e: e.timestamp),
        )


class _noop_ctx:
    """Wraps an existing client so it can be used with `async with`."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
