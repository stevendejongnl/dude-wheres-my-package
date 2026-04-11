import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

DPD_LOGIN_URL = "https://my.dpd.nl/api/login"
DPD_SHIPMENTS_URL = "https://my.dpd.nl/api/shipments"
DPD_TRACKING_URL = "https://tracking.dpd.de/status/nl_NL/parcel"

STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    ("afgeleverd", TrackingStatus.DELIVERED),
    ("delivered", TrackingStatus.DELIVERED),
    ("bezorgd", TrackingStatus.DELIVERED),
    ("onderweg naar ontvanger", TrackingStatus.OUT_FOR_DELIVERY),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("onderweg", TrackingStatus.IN_TRANSIT),
    ("in het dpd-depot", TrackingStatus.IN_TRANSIT),
    ("in het depot", TrackingStatus.IN_TRANSIT),
    ("zending aangekondigd", TrackingStatus.PRE_TRANSIT),
    ("aangemeld", TrackingStatus.PRE_TRANSIT),
    ("niet afgeleverd", TrackingStatus.FAILED_ATTEMPT),
    ("retour", TrackingStatus.RETURNED),
]


def _parse_status(text: str) -> TrackingStatus:
    lower = text.lower()
    for key, status in STATUS_MAP:
        if key in lower:
            return status
    return TrackingStatus.UNKNOWN


class DPD(CarrierBase):
    name = "dpd"
    auth_type = AuthType.CREDENTIALS

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def login(self, username: str, password: str) -> AuthTokens:
        async with self._get_client() as client:
            response = await client.post(
                DPD_LOGIN_URL,
                json={"username": username, "password": password},
            )
            response.raise_for_status()
            data = response.json()

        return AuthTokens(
            access_token=data.get("token", data.get("access_token", "")),
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        async with self._get_client() as client:
            response = await client.get(
                DPD_SHIPMENTS_URL,
                headers={"Authorization": f"Bearer {tokens.access_token}"},
            )
            response.raise_for_status()
            data = response.json()

        results: list[TrackingResult] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        for shipment in data.get("shipments", data.get("parcels", [])):
            tracking_number = shipment.get(
                "parcelNumber", shipment.get("trackingNumber", "unknown")
            )
            status_text = shipment.get("status", "")
            result = TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=_parse_status(status_text) if status_text else TrackingStatus.UNKNOWN,
            )
            results.append(result)

        return results

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        url = f"{DPD_TRACKING_URL}/{tracking_number}"

        async with self._get_client() as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html",
                },
                follow_redirects=True,
            )
            response.raise_for_status()

        return self._parse_html(tracking_number, response.text)

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_html(self, tracking_number: str, html: str) -> TrackingResult:
        soup = BeautifulSoup(html, "lxml")
        events: list[TrackingEvent] = []
        status = TrackingStatus.UNKNOWN

        status_el = soup.select_one(".status-info, .parcelStatus, [data-status]")
        if status_el:
            status_text = status_el.get_text(strip=True)
            status = _parse_status(status_text)

        for row in soup.select(
            ".parcel-event, .tracking-event, tr.event, .statusList li"
        ):
            date_el = row.select_one(".date, .event-date, td:nth-child(1)")
            desc_el = row.select_one(
                ".description, .event-description, td:nth-child(2)"
            )
            loc_el = row.select_one(".location, .event-location, td:nth-child(3)")

            description = desc_el.get_text(strip=True) if desc_el else ""
            location = loc_el.get_text(strip=True) if loc_el else None
            date_text = date_el.get_text(strip=True) if date_el else ""

            try:
                ts = datetime.fromisoformat(date_text) if date_text else datetime.now()
            except ValueError:
                ts = datetime.now()

            if description:
                events.append(
                    TrackingEvent(
                        timestamp=ts,
                        status=_parse_status(description),
                        description=description,
                        location=location or None,
                    )
                )

        if not status_el and events:
            status = events[-1].status

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
