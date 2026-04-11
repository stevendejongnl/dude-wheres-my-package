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

# DHL eCommerce NL endpoints
DHL_BASE = "https://my.dhlecommerce.nl"
DHL_LOGIN_URL = f"{DHL_BASE}/api/user/login"
DHL_PARCELS_URL = f"{DHL_BASE}/receiver-parcel-api/parcels"
DHL_TRACKING_URL = "https://www.dhl.com/shipmentTracking"

STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    ("delivered", TrackingStatus.DELIVERED),
    ("out_for_delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("door_delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("in_transit", TrackingStatus.IN_TRANSIT),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("transit", TrackingStatus.IN_TRANSIT),
    ("sorting", TrackingStatus.IN_TRANSIT),
    ("prenotification_received", TrackingStatus.PRE_TRANSIT),
    ("data_received", TrackingStatus.PRE_TRANSIT),
    ("pre-transit", TrackingStatus.PRE_TRANSIT),
    ("shipment information received", TrackingStatus.PRE_TRANSIT),
    ("failed_delivery", TrackingStatus.FAILED_ATTEMPT),
    ("failed delivery", TrackingStatus.FAILED_ATTEMPT),
    ("returned", TrackingStatus.RETURNED),
    ("returned_to_shipper", TrackingStatus.RETURNED),
]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_status(text: str) -> TrackingStatus:
    lower = text.lower()
    for key, status in STATUS_MAP:
        if key in lower:
            return status
    return TrackingStatus.UNKNOWN


class DHL(CarrierBase):
    name = "dhl"
    auth_type = AuthType.CREDENTIALS

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def login(self, username: str, password: str) -> AuthTokens:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # GET the login page first to get XSRF cookie
            await client.get(f"{DHL_BASE}/account/sign-in")
            xsrf = client.cookies.get("XSRF-TOKEN", "")

            response = await client.post(
                DHL_LOGIN_URL,
                json={"email": username, "password": password},
                headers={"x-xsrf-token": xsrf},
            )
            response.raise_for_status()

            # Store all cookies as the "token" — DHL uses cookie-based sessions
            cookies = {name: value for name, value in client.cookies.items()}

        return AuthTokens(
            access_token=cookies.get("XSRF-TOKEN", ""),
            refresh_token=None,
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        # DHL uses cookie sessions — we need to re-login each sync
        # The "access_token" field stores the XSRF token from the last login
        # But cookies expire, so we store email/password in the account and re-login

        # For now, use the stored XSRF token + cookie session approach
        async with self._get_client() as client:
            response = await client.get(
                DHL_PARCELS_URL,
                params={"tab": "incoming"},
                headers={
                    "Accept": "application/json",
                    "x-xsrf-token": tokens.access_token,
                },
            )
            response.raise_for_status()
            data = response.json()

        results: list[TrackingResult] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        for parcel in data.get("parcels", []):
            result = self._parse_parcel(parcel)
            if result.events:
                latest = max(e.timestamp for e in result.events)
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

        return self._parse_tracking_response(tracking_number, response.json())

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_parcel(self, parcel: dict) -> TrackingResult:
        barcode = parcel.get("barcode", "unknown")
        status_text = parcel.get("status", "")
        status = _parse_status(status_text)

        sender_name = parcel.get("sender", {}).get("name", "")

        events: list[TrackingEvent] = []

        created = parcel.get("createdAt")
        if created:
            try:
                events.append(TrackingEvent(
                    timestamp=_ensure_utc(datetime.fromisoformat(created)),
                    status=TrackingStatus.PRE_TRANSIT,
                    description=sender_name or "Shipment registered",
                ))
            except ValueError:
                pass

        receiving = parcel.get("receivingTimeIndication", {})
        if receiving and receiving.get("moment"):
            try:
                events.append(TrackingEvent(
                    timestamp=_ensure_utc(datetime.fromisoformat(receiving["moment"])),
                    status=TrackingStatus.DELIVERED,
                    description="Bezorgd",
                ))
            except ValueError:
                pass

        estimated = None
        if receiving and receiving.get("indicationType") != "MomentIndication":
            moment = receiving.get("moment")
            if moment:
                try:
                    estimated = _ensure_utc(datetime.fromisoformat(moment))
                except ValueError:
                    pass

        return TrackingResult(
            tracking_number=barcode,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=sorted(events, key=lambda e: e.timestamp),
        )

    def _parse_tracking_response(self, tracking_number: str, data: dict) -> TrackingResult:
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
                ts = _ensure_utc(datetime.fromisoformat(ts_str)) if ts_str else datetime.now(timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(description),
                    description=description,
                    location=", ".join(location_parts) or None,
                )
            )

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=sorted(events, key=lambda e: e.timestamp),
        )


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
