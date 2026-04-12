from datetime import UTC, datetime

import httpx

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

# GLS Group (NL) public tracking endpoint
GLS_TRACKING_URL = "https://gls-group.eu/app/service/open/rest/NL/nl/rstt001"
GLS_REFERER = "https://gls-group.eu/NL/nl/opvolging-van-pakketten"

STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    # Failed attempt — must precede "delivered" (substring match)
    ("the parcel could not be delivered", TrackingStatus.FAILED_ATTEMPT),
    ("not delivered", TrackingStatus.FAILED_ATTEMPT),
    ("niet bezorgd", TrackingStatus.FAILED_ATTEMPT),
    # Returned — must precede "delivered" (substring match)
    ("the parcel has been returned", TrackingStatus.RETURNED),
    ("retour", TrackingStatus.RETURNED),
    ("returned", TrackingStatus.RETURNED),
    # Delivered
    ("the parcel has been delivered", TrackingStatus.DELIVERED),
    ("het pakket is bezorgd", TrackingStatus.DELIVERED),
    ("afgeleverd", TrackingStatus.DELIVERED),
    ("bezorgd", TrackingStatus.DELIVERED),
    ("delivered", TrackingStatus.DELIVERED),
    # Out for delivery
    ("the parcel is out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("in delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("wordt vandaag bezorgd", TrackingStatus.OUT_FOR_DELIVERY),
    # In transit
    ("the parcel has left the parcel center", TrackingStatus.IN_TRANSIT),
    ("the parcel has reached the parcel center", TrackingStatus.IN_TRANSIT),
    ("the parcel is on its way", TrackingStatus.IN_TRANSIT),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("parcel center", TrackingStatus.IN_TRANSIT),
    ("sorteercentrum", TrackingStatus.IN_TRANSIT),
    ("onderweg", TrackingStatus.IN_TRANSIT),
    ("depot", TrackingStatus.IN_TRANSIT),
    # Pre-transit
    ("the parcel was handed over to gls", TrackingStatus.PRE_TRANSIT),
    ("the parcel data was entered", TrackingStatus.PRE_TRANSIT),
    ("preadvice", TrackingStatus.PRE_TRANSIT),
    ("aangemeld", TrackingStatus.PRE_TRANSIT),
    ("data received", TrackingStatus.PRE_TRANSIT),
]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_status(text: str) -> TrackingStatus:
    lower = text.lower()
    for key, status in STATUS_MAP:
        if key in lower:
            return status
    return TrackingStatus.UNKNOWN


class GLS(CarrierBase):
    name = "gls"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        async with self._get_client() as client:
            response = await client.post(
                GLS_TRACKING_URL,
                data={"match": tracking_number, "type": "MYFGLS"},
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    "Referer": GLS_REFERER,
                },
                follow_redirects=True,
            )
            if response.status_code == 404:
                return TrackingResult(
                    tracking_number=tracking_number,
                    carrier=self.name,
                    status=TrackingStatus.UNKNOWN,
                )
            response.raise_for_status()

        return self._parse_tracking_response(tracking_number, response.json())

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise NotImplementedError(
            "GLS account sync is not supported. "
            "Add parcels manually with the tracking number."
        )

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_tracking_response(
        self, tracking_number: str, data: dict
    ) -> TrackingResult:
        tu_status = data.get("tuStatus", [])
        if not tu_status:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        parcel = tu_status[0]
        events: list[TrackingEvent] = []

        for entry in parcel.get("history", []):
            date_str = entry.get("date", "")
            time_str = entry.get("time", "00:00")
            description = entry.get("evtDscr", "")

            try:
                ts = _ensure_utc(
                    datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                )
            except ValueError:
                try:
                    ts = _ensure_utc(
                        datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    )
                except ValueError:
                    ts = datetime.now(UTC)

            address = entry.get("address", {})
            location_parts = []
            if address.get("city"):
                location_parts.append(address["city"])
            if address.get("countryName"):
                location_parts.append(address["countryName"])

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(description),
                    description=description,
                    location=", ".join(location_parts) or None,
                )
            )

        sorted_events = sorted(events, key=lambda e: e.timestamp)

        # Overall status from progressBar or last event
        progress = parcel.get("progressBar", {})
        status_info = progress.get("statusInfo", "")
        status = _parse_status(status_info) if status_info else TrackingStatus.UNKNOWN
        if status == TrackingStatus.UNKNOWN and sorted_events:
            status = sorted_events[-1].status

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=sorted_events,
        )


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
