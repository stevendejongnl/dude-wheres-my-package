import logging
import os
import re
from datetime import UTC, datetime, timedelta

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

logger = logging.getLogger(__name__)

# DHL eCommerce NL endpoints
DHL_BASE = "https://my.dhlecommerce.nl"
DHL_LOGIN_URL = f"{DHL_BASE}/api/user/login"
DHL_PARCELS_URL = f"{DHL_BASE}/receiver-parcel-api/parcels"

# DHL Unified Tracking API (free key from developer.dhl.com)
DHL_UNIFIED_API = "https://api-eu.dhl.com/track/shipments"
DHL_API_KEY = os.environ.get("DHL_API_KEY", "")

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
        return dt.replace(tzinfo=UTC)
    return dt


# Direct mapping from the DHL Unified API statusCode field.
_STATUS_CODE_MAP: dict[str, TrackingStatus] = {
    "pre-transit": TrackingStatus.PRE_TRANSIT,
    "transit": TrackingStatus.IN_TRANSIT,
    "delivered": TrackingStatus.DELIVERED,
    "failure": TrackingStatus.FAILED_ATTEMPT,
    "unknown": TrackingStatus.UNKNOWN,
}


def _map_status_code(code: str) -> TrackingStatus:
    return _STATUS_CODE_MAP.get(code.lower(), TrackingStatus.UNKNOWN)


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

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        # kwargs is accepted for base-class compatibility — the service always
        # passes totp_secret="" since DHL eCommerce NL has no MFA flow. Ignored.
        async with httpx.AsyncClient(follow_redirects=True) as client:
            await client.get(f"{DHL_BASE}/account/sign-in")
            xsrf = client.cookies.get("XSRF-TOKEN", "")

            response = await client.post(
                DHL_LOGIN_URL,
                json={"email": username, "password": password},
                headers={"x-xsrf-token": xsrf},
            )
            response.raise_for_status()

        # Store credentials so we can re-login during sync (cookie sessions expire)
        return AuthTokens(
            access_token=f"{username}:{password}",
            refresh_token=None,
        )

    async def _login_and_fetch_parcels(self, tokens: AuthTokens) -> dict:
        """Login and fetch parcels in a single HTTP session (cookies don't transfer)."""
        parts = tokens.access_token.split(":", 1)
        email, password = parts[0], parts[1] if len(parts) > 1 else ""

        async with httpx.AsyncClient(follow_redirects=True) as client:
            await client.get(f"{DHL_BASE}/account/sign-in")
            xsrf = client.cookies.get("XSRF-TOKEN", "")

            response = await client.post(
                DHL_LOGIN_URL,
                json={"email": email, "password": password},
                headers={"x-xsrf-token": xsrf},
            )
            response.raise_for_status()

            xsrf = client.cookies.get("XSRF-TOKEN", xsrf)
            response = await client.get(
                DHL_PARCELS_URL,
                params={"tab": "incoming"},
                headers={
                    "Accept": "application/json",
                    "x-xsrf-token": xsrf,
                },
            )
            response.raise_for_status()
            return response.json()

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        data = await self._login_and_fetch_parcels(tokens)

        results: list[TrackingResult] = []
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

        for parcel in data.get("parcels", []):
            result = self._parse_parcel(parcel)
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest < cutoff:
                    continue
            results.append(result)

        return results

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        """Fetch full tracking timeline for a single DHL parcel.

        Uses the DHL Unified Tracking API when ``DHL_API_KEY`` is set
        (recommended — lightweight JSON, rich events). Falls back to
        Playwright scraping of dhl.com/tracking when no key is configured.
        """
        if DHL_API_KEY:
            return await self._track_via_api(tracking_number)
        return await self._track_via_playwright(tracking_number)

    async def _track_via_api(self, tracking_number: str) -> TrackingResult:
        """DHL Unified Tracking API — returns full event timeline as JSON."""
        async with self._get_client() as client:
            response = await client.get(
                DHL_UNIFIED_API,
                params={"trackingNumber": tracking_number},
                headers={
                    "DHL-API-Key": DHL_API_KEY,
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if response.status_code == 404:
                return TrackingResult(
                    tracking_number=tracking_number,
                    carrier=self.name,
                    status=TrackingStatus.UNKNOWN,
                )
            response.raise_for_status()

        return self._parse_unified_response(tracking_number, response.json())

    async def _track_via_playwright(self, tracking_number: str) -> TrackingResult:
        """Fallback: scrape dhl.com/tracking with Playwright."""
        from playwright.async_api import async_playwright

        from dwmp.carriers.browser import _USER_AGENT, _browser_lock, _launch_browser, _stealth

        url = (
            f"https://www.dhl.com/nl-en/home/tracking/tracking-parcel.html"
            f"?submit=1&tracking-id={tracking_number}"
        )

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
                    await page.goto(url, wait_until="networkidle", timeout=20_000)

                    # Wait for tracking events to render
                    try:
                        await page.wait_for_selector(
                            "[class*='timeline'], [class*='event'], "
                            "[class*='tracking-step']",
                            timeout=15_000,
                        )
                    except Exception:
                        logger.warning(
                            "DHL tracking events did not render for %s",
                            tracking_number,
                        )

                    html = await page.content()
                finally:
                    await browser.close()

        return self._parse_tracking_html(tracking_number, html)

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

    def _parse_unified_response(self, tracking_number: str, data: dict) -> TrackingResult:
        """Parse the DHL Unified Tracking API response."""
        shipments = data.get("shipments", [])
        if not shipments:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        shipment = shipments[0]

        # Top-level status from statusCode (more reliable than description text)
        status_code = shipment.get("status", {}).get("statusCode", "")
        status = _map_status_code(status_code)
        if status == TrackingStatus.UNKNOWN:
            status = _parse_status(
                shipment.get("status", {}).get("description", "")
            )

        events: list[TrackingEvent] = []
        for ev in shipment.get("events", []):
            ts_str = ev.get("timestamp", "")
            description = ev.get("description", "")
            # Strip HTML tags from descriptions (API sometimes includes <a> links)
            description = re.sub(r"<[^>]+>", "", description).strip()
            # Collapse whitespace left by tag removal
            description = re.sub(r"\s{2,}", " ", description)

            loc = ev.get("location", {})
            location = None
            if isinstance(loc, dict):
                locality = loc.get("address", {}).get("addressLocality", "")
                if locality:
                    location = locality

            try:
                ts = _ensure_utc(datetime.fromisoformat(ts_str)) if ts_str else no_date_fallback()
            except ValueError:
                ts = no_date_fallback()

            ev_status_code = ev.get("statusCode", "")
            ev_status = _map_status_code(ev_status_code)
            if ev_status == TrackingStatus.UNKNOWN:
                ev_status = _parse_status(description)

            events.append(TrackingEvent(
                timestamp=ts,
                status=ev_status,
                description=description,
                location=location,
            ))

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=sorted(events, key=lambda e: e.timestamp),
        )

    def _parse_tracking_html(self, tracking_number: str, html: str) -> TrackingResult:
        """Parse the dhl.com tracking page HTML (Playwright fallback)."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        events: list[TrackingEvent] = []
        status = TrackingStatus.UNKNOWN

        # DHL tracking page renders events in timeline steps
        for step in soup.select(
            "[class*='timeline-event'], [class*='tracking-step'], "
            "[class*='c-tracking-result--event']"
        ):
            desc = step.get_text(separator=" ", strip=True)
            if not desc:
                continue
            events.append(TrackingEvent(
                timestamp=no_date_fallback(),
                status=_parse_status(desc),
                description=desc[:200],
            ))

        if events:
            status = events[-1].status

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
