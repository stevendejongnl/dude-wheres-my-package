import httpx
from datetime import datetime, timedelta, timezone

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

# PostNL OAuth / API endpoints
POSTNL_STS_SERVER = "https://login.postnl.nl/101112a0-4a0f-4bbb-8176-2f1b2d370d7c"
POSTNL_CLIENT_ID = "bd9f1610-b56d-4e05-a09b-f696f05ddade"
POSTNL_AUTH_URL = f"{POSTNL_STS_SERVER}/auth-ui/v2/login"
POSTNL_TOKEN_URL = f"{POSTNL_STS_SERVER}/oauth2/v2.0/token"
POSTNL_API = "https://jouw.postnl.nl/web/api"
POSTNL_TRACK_URL = "https://jouw.postnl.nl/track-and-trace"

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

    async def refresh_tokens(self, tokens: AuthTokens) -> AuthTokens:
        if not tokens.refresh_token:
            raise ValueError("No refresh token available")

        async with self._get_client() as client:
            response = await client.post(
                POSTNL_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": POSTNL_CLIENT_ID,
                    "refresh_token": tokens.refresh_token,
                },
            )
            response.raise_for_status()
            data = response.json()

        expires_in = data.get("expires_in", 3600)
        return AuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", tokens.refresh_token),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        async with self._get_client() as client:
            response = await client.get(
                f"{POSTNL_API}/shipments",
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            response.raise_for_status()
            data = response.json()

        results: list[TrackingResult] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        for shipment in data.get("shipments", data.get("colli", [])):
            result = self._parse_shipment(shipment)
            # Filter by lookback window
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
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

    def _parse_shipment(self, shipment: dict) -> TrackingResult:
        tracking_number = shipment.get("barcode", shipment.get("key", "unknown"))
        return self._parse_json(tracking_number, shipment)

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

            ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()

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
