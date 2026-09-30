import base64
import logging
from dataclasses import replace
from datetime import UTC, datetime

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
    no_date_fallback,
)

logger = logging.getLogger(__name__)

# GoFo (gofo.com) is CIRRO Group's Dutch last-mile brand. Its tracking page
# is a WordPress site whose "cirro-tracking" plugin calls this endpoint
# directly from the browser — no auth, no signature — so we call it the same
# way. Found by reading window.CirroTrackingCps embedded in the page and
# tracking-results.js.
GOFO_TRACK_URL = "https://www.gofo.com/nl/open-api/official/track/queryTrackV2"

# processCode -> status, taken from tracking-results.js's progress-bar logic
# (the `completedSteps`/`progressType` switch), which is more granular than
# the coarse mapProcessCodeToLabel() used only for the UI's summary badge.
CODE_MAP: dict[str, TrackingStatus] = {
    "100": TrackingStatus.PRE_TRANSIT,  # Shipment not yet received/processed
    "282": TrackingStatus.PRE_TRANSIT,
    "200": TrackingStatus.IN_TRANSIT,  # Departed sorting center
    "201": TrackingStatus.IN_TRANSIT,  # Arrived in your region
    "202": TrackingStatus.IN_TRANSIT,  # Arrived at sorting center
    "203": TrackingStatus.IN_TRANSIT,  # In preparation for delivery
    "LS004": TrackingStatus.IN_TRANSIT,
    "LS006": TrackingStatus.IN_TRANSIT,
    "208": TrackingStatus.OUT_FOR_DELIVERY,  # Courier is on the way
    "281": TrackingStatus.OUT_FOR_DELIVERY,
    "205": TrackingStatus.DELIVERED,
    "204": TrackingStatus.EXCEPTION,
    "206": TrackingStatus.EXCEPTION,
    "300": TrackingStatus.EXCEPTION,
    "301": TrackingStatus.EXCEPTION,
    "257": TrackingStatus.RETURNED,
    "264": TrackingStatus.RETURNED,
}


def _parse_ts(s: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return no_date_fallback()


def _decrypt_pod_url(encrypted: str, pod_code: str, number: str) -> str | None:
    """Mirror gofo.com's client-side decryptTextWithKey(): AES-128-ECB with a
    key derived from (podCode + record.number), truncated or zero-padded to
    16 bytes. Decrypts to a plain, pre-signed S3 image URL — never raises,
    since a wrong/missing code should just mean no photos, not a crash."""
    combo = f"{pod_code}{number}"
    key_str = combo[:16] if len(combo) >= 16 else combo.ljust(16, "0")
    try:
        decryptor = Cipher(algorithms.AES(key_str.encode("utf-8")), modes.ECB()).decryptor()
        padded = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plain = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
        return plain.strip() or None
    except Exception:
        return None


class GoFo(CarrierBase):
    name = "gofo"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        # GoFo's own "pod code" is the last 6 digits of the consignee's phone
        # number. Tracking status works fine without it; it only unlocks
        # decrypting delivery-proof photos, so it's optional here — unlike
        # GLS/Trunkrs/DPD it's not required to get a result at all. Reused
        # as the generic postal_code kwarg/field for UI consistency.
        pod_code = kwargs.get("postal_code", "").strip()

        async with self._get_client() as client:
            response = await client.post(
                GOFO_TRACK_URL,
                json={"numberList": [tracking_number]},
                headers={"Content-Type": "application/json", "lang": "nl"},
            )
            response.raise_for_status()

        return self._parse_tracking_response(tracking_number, response.json(), pod_code)

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise NotImplementedError(
            "GoFo account sync is not supported. "
            "Add parcels manually with the tracking number."
        )

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_tracking_response(
        self, tracking_number: str, data: dict, pod_code: str = ""
    ) -> TrackingResult:
        records = data.get("data") or []
        wanted = tracking_number.strip().upper()
        record = next(
            (
                r
                for r in records
                if str(r.get("waybillNo", "")).strip().upper() == wanted
                or str(r.get("trackingNumber", "")).strip().upper() == wanted
            ),
            None,
        )
        if record is None:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        events: list[TrackingEvent] = []
        for evt in record.get("trackEventList") or []:
            code = str(evt.get("processCode") or "")
            description = evt.get("processContent") or evt.get("mainContent") or ""
            timestamp = evt.get("processDate") or ""
            events.append(
                TrackingEvent(
                    timestamp=_parse_ts(timestamp) if timestamp else no_date_fallback(),
                    status=CODE_MAP.get(code, TrackingStatus.UNKNOWN),
                    description=description,
                    location=evt.get("processLocation") or None,
                )
            )
        events.sort(key=lambda e: e.timestamp)

        last_code = str((record.get("lastTrackEvent") or {}).get("processCode") or "")
        status = CODE_MAP.get(last_code, TrackingStatus.UNKNOWN)
        if status == TrackingStatus.UNKNOWN and events:
            status = events[-1].status

        if status == TrackingStatus.DELIVERED and pod_code and events:
            events[-1] = self._attach_pod_photos(events[-1], record, pod_code)

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=events,
        )

    def _attach_pod_photos(
        self, event: TrackingEvent, record: dict, pod_code: str
    ) -> TrackingEvent:
        """Best-effort: decrypt podImgList into viewable photo URLs. Any
        failure (no images, wrong code, format change) just means the
        delivered event has no photos — never affects the tracking status."""
        img_list = record.get("podImgList") or []
        number = str(record.get("number") or "")
        if not img_list or not number:
            return event
        photos = [url for img in img_list if (url := _decrypt_pod_url(img, pod_code, number))]
        if not photos:
            return event
        return replace(event, proof_photos=photos)


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
