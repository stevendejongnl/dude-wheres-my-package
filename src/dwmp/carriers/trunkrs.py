import json
import re
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

TRUNKRS_BASE_URL = "https://parcel.trunkrs.nl"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)

STATE_MAP: dict[str, TrackingStatus] = {
    # Pre-transit
    "CREATED": TrackingStatus.PRE_TRANSIT,
    "DATA_RECEIVED": TrackingStatus.PRE_TRANSIT,
    "DATA_PROCESSED": TrackingStatus.PRE_TRANSIT,
    "PICKUP_DRIVER_ASSIGNED": TrackingStatus.PRE_TRANSIT,
    "PICKUP_ACCEPTED_BY_DRIVER": TrackingStatus.PRE_TRANSIT,
    # In transit
    "PICKUP_PICKED_UP": TrackingStatus.IN_TRANSIT,
    "LINEHAUL_IN_TRANSIT": TrackingStatus.IN_TRANSIT,
    "LINEHAUL_ARRIVED_ON_TIME": TrackingStatus.IN_TRANSIT,
    "SHIPMENT_SORTED": TrackingStatus.IN_TRANSIT,
    "SHIPMENT_SORTED_AT_SUB_DEPOT": TrackingStatus.IN_TRANSIT,
    "SHIPMENT_DELAYED": TrackingStatus.IN_TRANSIT,
    "SHIPMENT_DELAYED_AFTER_SORTED": TrackingStatus.IN_TRANSIT,
    "MIS_SORTED": TrackingStatus.IN_TRANSIT,
    # Out for delivery
    "SHIPMENT_ACCEPTED_BY_DRIVER": TrackingStatus.OUT_FOR_DELIVERY,
    # Delivered
    "SHIPMENT_DELIVERED": TrackingStatus.DELIVERED,
    "SHIPMENT_DELIVERED_TO_NEIGHBOR": TrackingStatus.DELIVERED,
    # Failed attempt
    "SHIPMENT_NOT_DELIVERED": TrackingStatus.FAILED_ATTEMPT,
    "RECIPIENT_NOT_AT_HOME": TrackingStatus.FAILED_ATTEMPT,
    "DELIVER_ADDRESS_NOT_ACCESSIBLE": TrackingStatus.FAILED_ATTEMPT,
    "MAX_FAILED_DELIVERY_ATTEMPT": TrackingStatus.FAILED_ATTEMPT,
    # Returned
    "REFUSED_BY_CUSTOMER": TrackingStatus.RETURNED,
    "RETURN_SHIPMENT_TO_SENDER": TrackingStatus.RETURNED,
    "RETURN_ACCEPTED_BY_SENDER": TrackingStatus.RETURNED,
    "RETURN_ACCEPTED_BY_TRUNKRS": TrackingStatus.RETURNED,
}


class Trunkrs(CarrierBase):
    name = "trunkrs"
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
        postal_code = postal_code.upper()
        url = f"{TRUNKRS_BASE_URL}/{tracking_number}/{postal_code}"
        async with self._get_client() as client:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                follow_redirects=False,
            )
        if response.is_redirect:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )
        response.raise_for_status()
        match = _NEXT_DATA_RE.search(response.text)
        if not match:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )
        return self._parse_tracking_response(tracking_number, json.loads(match.group(1)), url)

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise NotImplementedError(
            "Trunkrs account sync is not supported. "
            "Add parcels manually with the tracking number."
        )

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_tracking_response(
        self, tracking_number: str, data: dict, tracking_url: str | None = None
    ) -> TrackingResult:
        shipment = data.get("props", {}).get("pageProps", {}).get("shipment") or {}
        events: list[TrackingEvent] = []

        # Sender as a pre_transit event so _enrich_package() can extract it.
        sender_name = shipment.get("senderName") or shipment.get("merchantName")
        if sender_name:
            events.append(
                TrackingEvent(
                    timestamp=no_date_fallback(),
                    status=TrackingStatus.PRE_TRANSIT,
                    description=sender_name,
                    location=None,
                )
            )

        # auditLogs contain internal logistics notes (e.g. "[SORT]: Handsorted
        # at center"), not user-facing state transitions — skip them.

        current = shipment.get("currentState") or {}
        if current.get("stateName"):
            events.append(
                TrackingEvent(
                    timestamp=_parse_ts(current.get("setAt", "")),
                    status=STATE_MAP.get(current["stateName"], TrackingStatus.UNKNOWN),
                    description=_humanise(current["stateName"]),
                    location=None,
                )
            )

        status = STATE_MAP.get(current.get("stateName", ""), TrackingStatus.UNKNOWN)
        if status == TrackingStatus.UNKNOWN and events:
            status = events[-1].status

        # Delivery window from timeSlot: from/to is the narrow ETA band.
        estimated_delivery = None
        delivery_window_end = None
        time_slot = shipment.get("timeSlot") or {}
        if time_slot.get("from"):
            estimated_delivery = _parse_ts(time_slot["from"])
        if time_slot.get("to"):
            delivery_window_end = _parse_ts(time_slot["to"])

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=events,
            estimated_delivery=estimated_delivery,
            delivery_window_end=delivery_window_end,
            tracking_url=tracking_url,
        )


def _parse_ts(s: str) -> datetime:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return no_date_fallback()


def _humanise(state: str) -> str:
    return state.replace("_", " ").lower().capitalize()


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
