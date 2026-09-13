from datetime import UTC, datetime

import httpx

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    CarrierTransientError,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
    no_date_fallback,
)

# Cainiao Global — AliExpress's umbrella logistics tracker. AliExpress orders
# ship through dozens of different Chinese/last-mile carriers (Cainiao
# Warehouse, China Post, 4PX, Yanwen, PDN Express, YunExpress, ...) but every
# one of them reports into this single public API keyed by tracking/mail
# number, so one integration covers "AliExpress and basically anything
# shipped from China" without needing a carrier-specific scraper each.
CAINIAO_TRACKING_URL = "https://global.cainiao.com/global/detail.json"

# Ordered substring matches against Cainiao's English "standerdDesc" event
# text and stage group name (e.g. "In transit", "At customs"). Checked in
# order, most specific first — mirrors the GLS/Dragonfly carriers' approach
# since Cainiao doesn't expose a stable enum of status codes we can rely on.
STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    # Failed delivery attempt — must precede "deliver"
    ("delivery failed", TrackingStatus.FAILED_ATTEMPT),
    ("failed delivery", TrackingStatus.FAILED_ATTEMPT),
    ("unable to deliver", TrackingStatus.FAILED_ATTEMPT),
    ("delivery unsuccessful", TrackingStatus.FAILED_ATTEMPT),
    ("no one available", TrackingStatus.FAILED_ATTEMPT),
    # Returned — must precede "deliver"
    ("return to sender", TrackingStatus.RETURNED),
    ("returned", TrackingStatus.RETURNED),
    # Delivered — must precede generic "transit"/"processing"
    ("signed", TrackingStatus.DELIVERED),
    ("picked up by receiver", TrackingStatus.DELIVERED),
    ("successfully delivered", TrackingStatus.DELIVERED),
    ("delivered", TrackingStatus.DELIVERED),
    # Ready for pickup at a locker/collection point
    ("pickup point", TrackingStatus.READY_FOR_PICKUP),
    ("collection point", TrackingStatus.READY_FOR_PICKUP),
    ("locker", TrackingStatus.READY_FOR_PICKUP),
    # Out for delivery
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("delivery in progress", TrackingStatus.OUT_FOR_DELIVERY),
    ("courier is delivering", TrackingStatus.OUT_FOR_DELIVERY),
    # Exceptions
    ("exception", TrackingStatus.EXCEPTION),
    ("problem", TrackingStatus.EXCEPTION),
    ("delay", TrackingStatus.EXCEPTION),
    ("held at customs", TrackingStatus.EXCEPTION),
    ("seized", TrackingStatus.EXCEPTION),
    # Pre-transit — order placed / data received, not yet moving
    ("data received", TrackingStatus.PRE_TRANSIT),
    ("waybill", TrackingStatus.PRE_TRANSIT),
    ("order information", TrackingStatus.PRE_TRANSIT),
    ("waiting for", TrackingStatus.PRE_TRANSIT),
    # In transit (broad, checked last so more specific stages win above)
    ("customs", TrackingStatus.IN_TRANSIT),
    ("transit", TrackingStatus.IN_TRANSIT),
    ("departed", TrackingStatus.IN_TRANSIT),
    ("leaving", TrackingStatus.IN_TRANSIT),
    ("arrived", TrackingStatus.IN_TRANSIT),
    ("sorting", TrackingStatus.IN_TRANSIT),
    ("processing", TrackingStatus.IN_TRANSIT),
    ("hub", TrackingStatus.IN_TRANSIT),
    ("warehouse", TrackingStatus.IN_TRANSIT),
]


def _parse_status(text: str) -> TrackingStatus:
    lower = text.lower()
    for key, status in STATUS_MAP:
        if key in lower:
            return status
    return TrackingStatus.UNKNOWN


class Cainiao(CarrierBase):
    name = "cainiao"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        async with self._get_client() as client:
            try:
                response = await client.get(
                    CAINIAO_TRACKING_URL,
                    params={"mailNos": tracking_number, "lang": "en-US"},
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                    timeout=15,
                )
            except httpx.HTTPError as exc:
                raise CarrierTransientError(self.name, str(exc)) from exc

        if response.status_code != 200:
            raise CarrierTransientError(self.name, f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except Exception as exc:
            raise CarrierTransientError(self.name, f"bad JSON: {exc}") from exc

        if not payload.get("success"):
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        return self._parse_tracking_response(tracking_number, payload)

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise NotImplementedError(
            "Cainiao account sync is not supported. "
            "Add parcels manually with the tracking number."
        )

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_tracking_response(self, tracking_number: str, payload: dict) -> TrackingResult:
        modules = payload.get("module") or []
        if not modules:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        mod = modules[0]
        detail_list = mod.get("detailList") or []
        if not detail_list:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        events: list[TrackingEvent] = []
        for item in reversed(detail_list):  # API returns newest first
            ts_ms = item.get("time")
            if ts_ms:
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            else:
                ts = no_date_fallback()

            description = item.get("standerdDesc") or item.get("desc") or ""
            node_desc = (item.get("group") or {}).get("nodeDesc", "")

            events.append(
                TrackingEvent(
                    timestamp=ts,
                    status=_parse_status(f"{description} {node_desc}"),
                    description=description or node_desc or "Update",
                )
            )

        # progressPointList's final "Delivered" point is the definitive
        # delivered signal — more reliable than matching the latest event
        # text, whose exact wording for "signed for" varies by last-mile
        # carrier.
        progress_points = mod.get("processInfo", {}).get("progressPointList", [])
        delivered_point = next(
            (p for p in progress_points if p.get("pointName") == "Delivered"), None
        )

        if delivered_point and delivered_point.get("light"):
            status = TrackingStatus.DELIVERED
        elif events:
            status = events[-1].status
        else:
            status = TrackingStatus.UNKNOWN

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=events,
        )


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
