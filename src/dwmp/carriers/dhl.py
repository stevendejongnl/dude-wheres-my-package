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

# DHL OAuth / API endpoints
DHL_AUTH_URL = "https://api.dhl.com/authorize"
DHL_TOKEN_URL = "https://api.dhl.com/oauth/token"
DHL_SHIPMENTS_URL = "https://api.dhl.com/shipments"
DHL_TRACKING_URL = "https://www.dhl.com/shipmentTracking"

STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    ("delivered", TrackingStatus.DELIVERED),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("transit", TrackingStatus.IN_TRANSIT),
    ("processed", TrackingStatus.IN_TRANSIT),
    ("picked up", TrackingStatus.IN_TRANSIT),
    ("shipment information received", TrackingStatus.PRE_TRANSIT),
    ("pre-transit", TrackingStatus.PRE_TRANSIT),
    ("failed delivery", TrackingStatus.FAILED_ATTEMPT),
    ("returned", TrackingStatus.RETURNED),
]


def _parse_status(text: str) -> TrackingStatus:
    lower = text.lower()
    for key, status in STATUS_MAP:
        if key in lower:
            return status
    return TrackingStatus.UNKNOWN


class DHL(CarrierBase):
    name = "dhl"
    auth_type = AuthType.OAUTH

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def get_auth_url(self, callback_url: str) -> str:
        return (
            f"{DHL_AUTH_URL}"
            f"?redirect_uri={callback_url}"
            f"&response_type=code"
            f"&scope=shipments"
        )

    async def handle_callback(self, code: str, callback_url: str) -> AuthTokens:
        async with self._get_client() as client:
            response = await client.post(
                DHL_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback_url,
                },
            )
            response.raise_for_status()
            data = response.json()

        expires_in = data.get("expires_in", 3600)
        return AuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    async def refresh_tokens(self, tokens: AuthTokens) -> AuthTokens:
        if not tokens.refresh_token:
            raise ValueError("No refresh token available")

        async with self._get_client() as client:
            response = await client.post(
                DHL_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
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
                DHL_SHIPMENTS_URL,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            response.raise_for_status()
            data = response.json()

        results: list[TrackingResult] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        for shipment in data.get("shipments", data.get("results", [])):
            result = self._parse_shipment(shipment)
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                if latest < cutoff:
                    continue
            results.append(result)

        return results

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        params = {
            "trackingNumber": tracking_number,
            "language": "en",
            "requesterCountryCode": "NL",
        }

        async with self._get_client() as client:
            response = await client.get(
                DHL_TRACKING_URL,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )
            response.raise_for_status()

        return self._parse_response(tracking_number, response.json())

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_shipment(self, shipment: dict) -> TrackingResult:
        tracking_number = shipment.get("id", shipment.get("trackingNumber", "unknown"))
        return self._parse_response(tracking_number, {"results": [shipment]})

    def _parse_response(self, tracking_number: str, data: dict) -> TrackingResult:
        events: list[TrackingEvent] = []
        status = TrackingStatus.UNKNOWN

        shipments = data.get("results", data.get("shipments", []))
        if isinstance(shipments, list) and shipments:
            shipment = shipments[0]
        elif isinstance(shipments, dict):
            shipment = shipments
        else:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        status_text = shipment.get("status", {}).get("description", "")
        if status_text:
            status = _parse_status(status_text)

        for cp in shipment.get("events", shipment.get("checkpoints", [])):
            ts_str = cp.get("timestamp") or cp.get("date", "")
            description = cp.get("description", cp.get("statusDescription", ""))
            location_parts = []
            loc = cp.get("location", {})
            if isinstance(loc, dict):
                if loc.get("address", {}).get("addressLocality"):
                    location_parts.append(loc["address"]["addressLocality"])
            elif isinstance(loc, str):
                location_parts.append(loc)

            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
            except ValueError:
                ts = datetime.now()

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(description),
                    description=description,
                    location=", ".join(location_parts) or None,
                )
            )

        estimated_str = shipment.get("estimatedDeliveryDate") or shipment.get(
            "estimatedTimeOfDelivery"
        )
        estimated = None
        if estimated_str:
            try:
                estimated = datetime.fromisoformat(estimated_str)
            except ValueError:
                pass

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=sorted(events, key=lambda e: e.timestamp),
        )


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
