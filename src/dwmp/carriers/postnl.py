import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

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
POSTNL_TRACK_API_URL = "https://jouw.postnl.nl/track-and-trace/api/trackAndTrace"

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

# detailsUrl from PostNL has been seen in two formats:
#   https://jouw.postnl.nl/track-and-trace/{barcode}/{postal_code}/{country}
#   https://jouw.postnl.nl/track-and-trace/{barcode}-{country}-{postal_code}
#
# We normalize both so account sync can hydrate the richer public timeline and
# later refreshes can keep working after the package falls off the account list.
_DETAILS_URL_HYPHEN_RE = re.compile(
    r"^(?P<barcode>[^-]+)-(?P<country>[A-Z]{2})-(?P<postal_code>[A-Za-z0-9]{4,10})$"
)


def _details_from_tracking_url(details_url: str | None) -> tuple[str | None, str | None, str | None]:
    if not details_url:
        return None, None, None

    path = urlparse(details_url).path.rstrip("/")
    marker = "/track-and-trace/"
    if marker not in path:
        return None, None, None

    slug = path.split(marker, 1)[1]
    parts = slug.split("/")

    if len(parts) >= 3:
        return parts[1], parts[2].upper(), f"{POSTNL_TRACK_URL}/{parts[0]}-{parts[2].upper()}-{parts[1]}"

    if parts:
        match = _DETAILS_URL_HYPHEN_RE.match(parts[0])
        if match:
            return (
                match.group("postal_code"),
                match.group("country"),
                f"{POSTNL_TRACK_URL}/{parts[0]}",
            )

    return None, None, None


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
    auth_type = AuthType.BROWSER_PAYLOAD

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
            result = await self._enrich_active_shipment(result)
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest < cutoff:
                    continue
            results.append(result)

        return results

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        tracking_url = kwargs.get("tracking_url", "")
        postal_code = kwargs.get("postal_code", "")
        country = kwargs.get("country", "")

        if tracking_url:
            url_postal_code, url_country, _ = _details_from_tracking_url(tracking_url)
            postal_code = postal_code or url_postal_code or ""
            country = country or url_country or ""

        country = country or "NL"
        if not postal_code:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
                tracking_url=tracking_url or None,
            )

        key = f"{tracking_number}-{country}-{postal_code}"
        url = f"{POSTNL_TRACK_API_URL}/{key}"

        async with self._get_client() as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    "Accept-Language": "nl-NL",
                },
                params={"language": "nl"},
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
        postal_code, _country, tracking_url = _details_from_tracking_url(shipment.get("detailsUrl"))

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

        window_end = None
        window_to = shipment.get("deliveryWindowTo")
        if window_to:
            try:
                window_end = datetime.fromisoformat(window_to)
            except ValueError:
                pass

        return TrackingResult(
            tracking_number=barcode,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            delivery_window_end=window_end,
            events=sorted(events, key=lambda e: e.timestamp),
            postal_code=postal_code,
            tracking_url=tracking_url,
        )

    def _parse_json(self, tracking_number: str, data: dict) -> TrackingResult:
        events: list[TrackingEvent] = []
        status = TrackingStatus.UNKNOWN

        colli = self._extract_colli(tracking_number, data)

        status_text = colli.get("statusPhase", {}).get("message", "")
        if status_text:
            status = _parse_status(status_text)

        latest_ts = colli.get("lastObservation")
        if latest_ts and status_text:
            events.append(
                TrackingEvent(
                    timestamp=_ensure_utc(datetime.fromisoformat(latest_ts)),
                    status=status,
                    description=status_text,
                )
            )

        for event_data in colli.get("events", []) + colli.get("observations", []):
            ts_str = (
                event_data.get("observationDate")
                or event_data.get("dateTime")
                or event_data.get("timestamp", "")
            )
            description = event_data.get("description", "")
            location = event_data.get("location", {})
            location_str = location.get("name") if isinstance(location, dict) else None

            ts = _ensure_utc(datetime.fromisoformat(ts_str)) if ts_str else no_date_fallback()

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(description),
                    description=description,
                    location=location_str,
                )
            )

        deduped_events = list({
            (event.timestamp, event.status, event.description): event
            for event in events
        }.values())

        estimated = colli.get("expectedDeliveryDate") or colli.get("eta", {}).get("start")
        window_end_raw = colli.get("eta", {}).get("end")
        postal_code = (
            colli.get("deliveryAddress", {})
            .get("address", {})
            .get("postalCode")
        )
        identification = colli.get("identification")
        _, _, tracking_url = _details_from_tracking_url(
            f"{POSTNL_TRACK_URL}/{identification}" if identification else None
        )

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            estimated_delivery=_ensure_utc(datetime.fromisoformat(estimated)) if estimated else None,
            delivery_window_end=_ensure_utc(datetime.fromisoformat(window_end_raw)) if window_end_raw else None,
            events=sorted(deduped_events, key=lambda e: e.timestamp),
            postal_code=postal_code,
            tracking_url=tracking_url,
        )

    def _extract_colli(self, tracking_number: str, data: dict) -> dict:
        colli = data if isinstance(data, dict) else {}
        if "colli" in colli:
            colli = colli["colli"]

        if isinstance(colli, list):
            return colli[0] if colli else {}

        if isinstance(colli, dict) and "statusPhase" not in colli:
            match = colli.get(tracking_number)
            if isinstance(match, dict):
                return match
            first = next((value for value in colli.values() if isinstance(value, dict)), {})
            return first

        return colli if isinstance(colli, dict) else {}

    def _parse_browser_payload(
        self, payload: dict, lookback_days: int = 30
    ) -> list[TrackingResult]:
        shipments = payload.get("shipments", [])
        details = payload.get("details", [])

        results_by_tracking: dict[str, TrackingResult] = {}
        for shipment in shipments:
            result = self._parse_graphql_shipment(shipment)
            results_by_tracking[result.tracking_number] = result

        for detail in details:
            tracking_number = detail.get("tracking_number")
            data = detail.get("data")
            if not tracking_number or not isinstance(data, dict):
                continue
            results_by_tracking[tracking_number] = self._parse_json(
                tracking_number, data
            )

        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
        filtered: list[TrackingResult] = []
        for result in results_by_tracking.values():
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest < cutoff:
                    continue
            filtered.append(result)
        return filtered

    async def _enrich_active_shipment(self, result: TrackingResult) -> TrackingResult:
        if result.status in (TrackingStatus.DELIVERED, TrackingStatus.RETURNED):
            return result
        if not (result.postal_code or result.tracking_url):
            return result

        enriched = await self.track(
            result.tracking_number,
            postal_code=result.postal_code or "",
            tracking_url=result.tracking_url or "",
        )
        if enriched.status == TrackingStatus.UNKNOWN and not enriched.events:
            return result

        return TrackingResult(
            tracking_number=result.tracking_number,
            carrier=self.name,
            status=enriched.status,
            estimated_delivery=enriched.estimated_delivery or result.estimated_delivery,
            delivery_window_end=enriched.delivery_window_end or result.delivery_window_end,
            events=enriched.events or result.events,
            postal_code=result.postal_code or enriched.postal_code,
            tracking_url=result.tracking_url or enriched.tracking_url,
        )


class _noop_ctx:
    """Wraps an existing client so it can be used with `async with`."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
