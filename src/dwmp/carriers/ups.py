"""UPS carrier via the official Track API (developer.ups.com).

Track-only, like GLS — no account sync. Requires UPS_CLIENT_ID and
UPS_CLIENT_SECRET (free developer.ups.com app); without them track()
returns an empty UNKNOWN result, which the downgrade guard treats as
"couldn't resolve" so stored statuses survive.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from dwmp.carriers._retry import with_retries
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

UPS_OAUTH_URL = "https://onlinetools.ups.com/security/v1/oauth/token"
UPS_TRACK_URL = "https://onlinetools.ups.com/api/track/v1/details"
UPS_CLIENT_ID = os.environ.get("UPS_CLIENT_ID", "")
UPS_CLIENT_SECRET = os.environ.get("UPS_CLIENT_SECRET", "")

# Activity status "type" field codes from the Track API.
_STATUS_TYPE_MAP: dict[str, TrackingStatus] = {
    "D": TrackingStatus.DELIVERED,
    "I": TrackingStatus.IN_TRANSIT,
    "P": TrackingStatus.IN_TRANSIT,
    "M": TrackingStatus.PRE_TRANSIT,
    "X": TrackingStatus.EXCEPTION,
    "RS": TrackingStatus.RETURNED,
    "O": TrackingStatus.OUT_FOR_DELIVERY,
}

# Description-text fallback, most specific first.
STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("delivered", TrackingStatus.DELIVERED),
    ("returned to", TrackingStatus.RETURNED),
    ("exception", TrackingStatus.EXCEPTION),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("departed", TrackingStatus.IN_TRANSIT),
    ("arrived", TrackingStatus.IN_TRANSIT),
    ("picked up", TrackingStatus.IN_TRANSIT),
    ("shipment information received", TrackingStatus.PRE_TRANSIT),
    ("label created", TrackingStatus.PRE_TRANSIT),
]


def _map_status(type_code: str | None, description: str) -> TrackingStatus:
    status = _STATUS_TYPE_MAP.get((type_code or "").upper())
    if status:
        return status
    lower = description.lower()
    for key, mapped in STATUS_MAP:
        if key in lower:
            return mapped
    return TrackingStatus.UNKNOWN


def _parse_activity_ts(date_str: str, time_str: str) -> datetime:
    """Track API dates are YYYYMMDD + HHMMSS with no timezone."""
    try:
        return datetime.strptime(
            f"{date_str}{(time_str or '000000'):0>6}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=UTC)
    except ValueError:
        return no_date_fallback()


def _parse_web_ts(gmt_date: str, gmt_time: str) -> datetime:
    """Web GetStatus gmtDate/gmtTime: YYYYMMDD + HH:MM:SS in GMT.

    The `date`/`time` fields are locale-formatted (DD/MM vs MM/DD, 12/24h
    depending on loc=) — never parse those.
    """
    try:
        return datetime.strptime(
            f"{gmt_date} {gmt_time or '00:00:00'}", "%Y%m%d %H:%M:%S"
        ).replace(tzinfo=UTC)
    except ValueError:
        return no_date_fallback()


@asynccontextmanager
async def _noop_ctx(client: httpx.AsyncClient):
    yield client


class UPS(CarrierBase):
    name = "ups"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client = http_client
        self._client_id = UPS_CLIENT_ID if client_id is None else client_id
        self._client_secret = UPS_CLIENT_SECRET if client_secret is None else client_secret
        self._token = ""
        self._token_deadline = 0.0

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        # The official API needs a paying UPS account; without creds fall back
        # to scraping the public track page (same approach as DHL's fallback).
        if not (self._client_id and self._client_secret):
            return await self._track_via_browser(tracking_number)

        token = await self._get_token()

        async def _do_request() -> httpx.Response:
            async with self._get_client() as client:
                resp = await client.get(
                    f"{UPS_TRACK_URL}/{tracking_number}",
                    params={"locale": "en_NL", "returnMilestones": "false"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "transId": tracking_number,
                        "transactionSrc": "dwmp",
                        "Accept": "application/json",
                    },
                    timeout=httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0),
                )
                if resp.status_code == 404:
                    return resp
                resp.raise_for_status()
                return resp

        response = await with_retries(_do_request, carrier=self.name)
        if response.status_code == 404:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )
        return self._parse_track_response(tracking_number, response.json())

    async def _track_via_browser(self, tracking_number: str) -> TrackingResult:
        """Scrape ups.com/track with Playwright and intercept the page's own
        GetStatus XHR — plain httpx is blocked by UPS's TLS fingerprinting."""
        from playwright.async_api import async_playwright

        from dwmp.carriers.browser import (
            _USER_AGENT,
            _browser_lock,
            _launch_browser,
            _stealth,
        )

        url = f"https://www.ups.com/track?loc=en_NL&tracknum={tracking_number}"

        async with _browser_lock:
            async with _stealth(_USER_AGENT).use_async(async_playwright()) as pw:
                browser = await _launch_browser(pw)
                try:
                    context = await browser.new_context(
                        user_agent=_USER_AGENT,
                        viewport={"width": 1280, "height": 800},
                        locale="en-GB",
                        timezone_id="Europe/Amsterdam",
                    )
                    page = await context.new_page()
                    async with page.expect_response(
                        lambda r: "track/api/Track/GetStatus" in r.url,
                        timeout=30_000,
                    ) as response_info:
                        await page.goto(
                            url, wait_until="domcontentloaded", timeout=30_000
                        )
                    response = await response_info.value
                    data = await response.json()
                except Exception:
                    logger.warning(
                        "UPS track page scrape failed for %s", tracking_number,
                        exc_info=True,
                    )
                    return TrackingResult(
                        tracking_number=tracking_number,
                        carrier=self.name,
                        status=TrackingStatus.UNKNOWN,
                    )
                finally:
                    await browser.close()

        return self._parse_web_json(tracking_number, data)

    def _parse_web_json(self, tracking_number: str, data: dict) -> TrackingResult:
        """Parse the ups.com web GetStatus JSON (unofficial, page-internal)."""
        details = (data.get("trackDetails") or [None])[0] or {}
        if not details:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        events: list[TrackingEvent] = []
        for activity in details.get("shipmentProgressActivities") or []:
            description = (activity.get("activityScan") or "").strip()
            if not description:
                continue
            events.append(TrackingEvent(
                timestamp=_parse_web_ts(
                    activity.get("gmtDate", ""), activity.get("gmtTime", "")
                ),
                status=_map_status(
                    activity.get("trackingStatusType"), description
                ),
                description=description,
                location=(activity.get("location") or "").strip() or None,
            ))
        events.sort(key=lambda e: e.timestamp)

        status = _map_status(
            details.get("packageStatusType", ""),
            details.get("packageStatus", ""),
        )
        if status == TrackingStatus.UNKNOWN and events:
            status = events[-1].status

        # scheduledDeliveryDate is locale-formatted; we always request
        # loc=en_NL so it's DD/MM/YYYY.
        estimated = None
        sched = details.get("scheduledDeliveryDate", "")
        if sched:
            try:
                estimated = datetime.strptime(sched, "%d/%m/%Y").replace(tzinfo=UTC)
            except ValueError:
                pass

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=events,
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise NotImplementedError(
            "UPS account sync is not supported. "
            "Add parcels manually with the tracking number."
        )

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_deadline:
            return self._token

        async def _do_request() -> httpx.Response:
            async with self._get_client() as client:
                resp = await client.post(
                    UPS_OAUTH_URL,
                    data={"grant_type": "client_credentials"},
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                    timeout=httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0),
                )
                resp.raise_for_status()
                return resp

        response = await with_retries(_do_request, carrier=self.name)
        data = response.json()
        self._token = data.get("access_token", "")
        # ponytail: 60s skew instead of tracking 401s — token lasts ~4h
        self._token_deadline = time.monotonic() + float(data.get("expires_in", 0)) - 60
        return self._token

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_track_response(
        self, tracking_number: str, data: dict
    ) -> TrackingResult:
        shipments = data.get("trackResponse", {}).get("shipment", []) or []
        packages = shipments[0].get("package", []) if shipments else []
        if not packages:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )
        pkg = packages[0]

        events: list[TrackingEvent] = []
        for activity in pkg.get("activity", []) or []:
            act_status = activity.get("status", {}) or {}
            description = act_status.get("description", "").strip()
            address = (activity.get("location", {}) or {}).get("address", {}) or {}
            location_parts = [
                p for p in [address.get("city", ""), address.get("countryCode", "")] if p
            ]
            events.append(TrackingEvent(
                timestamp=_parse_activity_ts(
                    activity.get("date", ""), activity.get("time", "")
                ),
                status=_map_status(act_status.get("type", ""), description),
                description=description or "Status update",
                location=", ".join(location_parts) or None,
            ))
        events.sort(key=lambda e: e.timestamp)

        status = TrackingStatus.UNKNOWN
        current = pkg.get("currentStatus", {}) or {}
        if current.get("description"):
            status = _map_status("", current["description"])
        if status == TrackingStatus.UNKNOWN and events:
            status = events[-1].status

        estimated = None
        for delivery_date in pkg.get("deliveryDate", []) or []:
            date_str = delivery_date.get("date", "")
            if date_str:
                time_str = (pkg.get("deliveryTime", {}) or {}).get("endTime", "")
                estimated = _parse_activity_ts(date_str, time_str)
                break

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=events,
        )
