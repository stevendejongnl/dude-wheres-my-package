from datetime import UTC, datetime

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

# GLS Netherlands tracking API (apm.gls.nl)
GLS_TRACKING_URL = "https://apm.gls.nl/api/tracktrace/v1"
GLS_REFERER = "https://www.gls-info.nl/"

STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    # Failed attempt — must precede "afgeleverd" (substring match)
    ("niet afgeleverd", TrackingStatus.FAILED_ATTEMPT),
    ("niet bezorgd", TrackingStatus.FAILED_ATTEMPT),
    ("could not be delivered", TrackingStatus.FAILED_ATTEMPT),
    # Returned — must precede "afgeleverd"
    ("retour", TrackingStatus.RETURNED),
    ("returned", TrackingStatus.RETURNED),
    # Delivered
    ("afgeleverd", TrackingStatus.DELIVERED),
    ("bezorgd", TrackingStatus.DELIVERED),
    ("delivered", TrackingStatus.DELIVERED),
    # Out for delivery
    ("geladen voor aflevering", TrackingStatus.OUT_FOR_DELIVERY),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("in delivery", TrackingStatus.OUT_FOR_DELIVERY),
    # In transit
    ("doorgestuurd naar gls depot", TrackingStatus.IN_TRANSIT),
    ("aangekomen op gls depot", TrackingStatus.IN_TRANSIT),
    ("ontvangen door gls", TrackingStatus.IN_TRANSIT),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("parcel center", TrackingStatus.IN_TRANSIT),
    ("sorteercentrum", TrackingStatus.IN_TRANSIT),
    ("depot", TrackingStatus.IN_TRANSIT),
    # Pre-transit
    ("aangekondigd bij gls", TrackingStatus.PRE_TRANSIT),
    ("gereed voor overdracht", TrackingStatus.PRE_TRANSIT),
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
        postal_code = kwargs.get("postal_code", "")
        if not postal_code:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        url = (
            f"{GLS_TRACKING_URL}/{tracking_number}"
            f"/postalcode/{postal_code}/details/nl-NL"
        )

        async with self._get_client() as client:
            response = await client.get(
                url,
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
        scans = data.get("scans", [])
        events: list[TrackingEvent] = []

        for scan in scans:
            description = scan.get("eventReasonDescr", "")
            date_str = scan.get("dateTime", "")
            depot_name = scan.get("depotName") or ""
            country = scan.get("countryName") or ""

            try:
                ts = _ensure_utc(datetime.fromisoformat(date_str))
            except ValueError:
                ts = no_date_fallback()

            location_parts = [p for p in [depot_name, country] if p and p != "-"]

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(description),
                    description=description,
                    location=", ".join(location_parts) or None,
                )
            )

        sorted_events = sorted(events, key=lambda e: e.timestamp)

        # Determine overall status from delivery info or last event
        delivery_info = data.get("deliveryScanInfo", {})
        if delivery_info.get("isDelivered"):
            status = TrackingStatus.DELIVERED
        elif sorted_events:
            status = sorted_events[-1].status
        else:
            status = TrackingStatus.UNKNOWN

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
