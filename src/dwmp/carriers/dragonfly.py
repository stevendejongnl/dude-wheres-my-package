from datetime import UTC, datetime

import httpx

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
    CarrierTransientError,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

DRAGONFLY_API = "https://dragonflyshipping.nl/cfworker/v3/tracking"

# Numeric status codes from the Dragonfly API.
# Observed values from live tracking; unmapped codes fall back to IN_TRANSIT.
STATUS_MAP: dict[int, TrackingStatus] = {
    0: TrackingStatus.PRE_TRANSIT,    # Data received
    100: TrackingStatus.PRE_TRANSIT,  # Pending pickup
    105: TrackingStatus.IN_TRANSIT,   # Received at facility
    106: TrackingStatus.IN_TRANSIT,   # Hub station inbound scan
    110: TrackingStatus.IN_TRANSIT,   # In transit between hubs
    200: TrackingStatus.OUT_FOR_DELIVERY,  # Out for delivery
    210: TrackingStatus.OUT_FOR_DELIVERY,  # Driver assigned
    300: TrackingStatus.DELIVERED,    # Delivered
    301: TrackingStatus.DELIVERED,    # Delivered to neighbour
    400: TrackingStatus.FAILED_ATTEMPT,
    401: TrackingStatus.FAILED_ATTEMPT,
    500: TrackingStatus.RETURNED,
    501: TrackingStatus.RETURNED,
    900: TrackingStatus.EXCEPTION,
}


class Dragonfly(CarrierBase):
    name = "dragonfly"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        url = f"{DRAGONFLY_API}/{tracking_number}/"
        async with self._get_client() as client:
            try:
                response = await client.get(url, timeout=15)
            except httpx.HTTPError as exc:
                raise CarrierTransientError(self.name, str(exc)) from exc

        if response.status_code == 404:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )
        if response.status_code != 200:
            raise CarrierTransientError(
                self.name, f"HTTP {response.status_code}"
            )

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

        result = payload.get("data", {}).get("result", {})
        return self._parse_result(tracking_number, result)

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise CarrierAuthError(
            self.name,
            "Dragonfly has no account portal — packages are discovered via "
            "Amazon sync and tracked individually by tracking number.",
        )

    def _parse_result(self, tracking_number: str, result: dict) -> TrackingResult:
        last = result.get("last_status", {})
        status_code = last.get("status", -1)
        is_delivered = last.get("isDelivered", False)

        if is_delivered:
            status = TrackingStatus.DELIVERED
        else:
            status = STATUS_MAP.get(status_code, TrackingStatus.IN_TRANSIT)

        # ETA
        eta_str = result.get("eta") or result.get("buffered_eta")
        estimated: datetime | None = None
        if eta_str:
            try:
                estimated = datetime.fromisoformat(eta_str).astimezone(UTC).replace(tzinfo=UTC)
            except ValueError:
                pass

        # Events from status_list (newest first in API response)
        events: list[TrackingEvent] = []
        for item in reversed(result.get("status_list", [])):
            ts_ms = item.get("timestamp")
            if ts_ms:
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            else:
                ts = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

            item_code = item.get("status", -1)
            item_delivered = item.get("isDelivered", False)
            if item_delivered:
                item_status = TrackingStatus.DELIVERED
            else:
                item_status = STATUS_MAP.get(item_code, TrackingStatus.IN_TRANSIT)

            labels = item.get("labels", {})
            short_en = labels.get("shortLabel", {}).get("en", "") or item.get("label", "")
            city = (item.get("package_location") or {}).get("address", {}).get("city", "")

            description = short_en.replace("{city}", city) if city else short_en
            if not description:
                description = item.get("label", str(item_code))

            events.append(TrackingEvent(
                timestamp=ts,
                status=item_status,
                description=description,
                location=city or None,
            ))

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=events,
        )

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
