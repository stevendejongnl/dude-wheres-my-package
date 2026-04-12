import re
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

# DPD Group (NL) endpoints
DPD_BASE = "https://www.dpdgroup.com"
DPD_LOGIN_START = f"{DPD_BASE}/nl/mydpd/login"
DPD_PARCELS_URL = f"{DPD_BASE}/nl/mydpd/my-parcels/incoming"
DPD_TRACKING_URL = "https://tracking.dpd.de/status/nl_NL/parcel"

# Keycloak endpoints (discovered from login redirect)
KC_BASE = "https://login.dpdgroup.com/auth/realms/login"
KC_TOKEN_URL = f"{KC_BASE}/protocol/openid-connect/token"

STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    ("delivered", TrackingStatus.DELIVERED),
    ("afgeleverd", TrackingStatus.DELIVERED),
    ("bezorgd", TrackingStatus.DELIVERED),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("onderweg naar ontvanger", TrackingStatus.OUT_FOR_DELIVERY),
    ("in transit", TrackingStatus.IN_TRANSIT),
    ("onderweg", TrackingStatus.IN_TRANSIT),
    ("depot", TrackingStatus.IN_TRANSIT),
    ("transported", TrackingStatus.IN_TRANSIT),
    ("arrived", TrackingStatus.IN_TRANSIT),
    ("exchanging data", TrackingStatus.PRE_TRANSIT),
    ("zending aangekondigd", TrackingStatus.PRE_TRANSIT),
    ("aangemeld", TrackingStatus.PRE_TRANSIT),
    ("niet afgeleverd", TrackingStatus.FAILED_ATTEMPT),
    ("retour", TrackingStatus.RETURNED),
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


class DPD(CarrierBase):
    name = "dpd"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        # DPD has Cloudflare bot protection — httpx can't bypass the JS challenge.
        # The access_token stores the page HTML captured from the browser session.
        # The sync is triggered after Playwright captures fresh HTML.
        html = tokens.access_token
        if not html or not html.startswith("<"):
            raise ValueError(
                "DPD requires browser-captured HTML. "
                "Use Playwright to log in and capture the parcels page."
            )
        return self._parse_parcels_page(html, lookback_days)

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

        return self._parse_tracking_page(tracking_number, response.text)

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_parcels_page(
        self, html: str, lookback_days: int = 30
    ) -> list[TrackingResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[TrackingResult] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        # Find parcel links — handle both normal and escaped class attributes
        parcel_links = soup.select("a[href*='parcelNumber']")
        parcel_links = [l for l in parcel_links if "mailto" not in l.get("href", "")]

        for link in parcel_links:
            href = link.get("href", "")
            match = re.search(r"parcelNumber=(\w+)", href)
            if not match:
                continue

            tracking_number = match.group(1)

            # Sender name
            sender_el = link.select_one(".parcelAlias, .sender-text h4")
            sender = sender_el.get_text(strip=True) if sender_el else ""
            sender = sender.replace("Parcel from ", "").strip()

            result = TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.IN_TRANSIT,
            )
            results.append(result)

        # If current parcel details are shown on the page, parse those too
        parcel_number_el = (
            soup.select_one(".parcelNumber")
            or soup.find(class_=re.compile(r"parcelNumber"))
        )
        if parcel_number_el:
            barcode = parcel_number_el.get_text(strip=True)

            # Parse status
            status_box = (
                soup.select_one(".parcelStatusBox")
                or soup.find(class_=re.compile(r"parcelStatusBox"))
            )
            status = TrackingStatus.UNKNOWN
            if status_box:
                status_icon = status_box.find(class_=re.compile(r"status-icon"))
                if status_icon:
                    classes = " ".join(status_icon.get("class", []))
                    status = _parse_status(classes)

                status_text_el = status_box.find(class_=re.compile(r"status-text"))
                if status_text_el and status == TrackingStatus.UNKNOWN:
                    status = _parse_status(status_text_el.get_text())

            # Parse tracking events
            events: list[TrackingEvent] = []
            # Events are in text format: "11.04.2026 , 03:49\nOirschot, NL\n\nDescription"
            tracking_section = (
                soup.select_one(".parcelDetailsBox")
                or soup.find(class_=re.compile(r"parcelDetailsBox"))
            )
            if tracking_section:
                event_items = tracking_section.select("li, .tracking-event, tr")
                for item in event_items:
                    text = item.get_text(separator="\n", strip=True)
                    event = self._parse_event_text(text)
                    if event:
                        events.append(event)

            # If no structured events found, try parsing from body text
            if not events:
                body_text = soup.get_text(separator="\n", strip=True)
                tracking_idx = body_text.find("Tracking details")
                if tracking_idx > -1:
                    # Stop at FAQ section
                    faq_idx = body_text.find("FAQ", tracking_idx)
                    end = faq_idx if faq_idx > tracking_idx else tracking_idx + 2000
                    tracking_text = body_text[tracking_idx:end]
                    events = self._parse_tracking_text(tracking_text)

            sorted_events = sorted(events, key=lambda e: e.timestamp)
            if status == TrackingStatus.UNKNOWN and sorted_events:
                status = sorted_events[-1].status

            # Update the matching result or add new one
            found = False
            for i, r in enumerate(results):
                if r.tracking_number == barcode:
                    results[i] = TrackingResult(
                        tracking_number=barcode,
                        carrier=self.name,
                        status=status,
                        events=sorted_events,
                    )
                    found = True
                    break

            if not found:
                results.append(TrackingResult(
                    tracking_number=barcode,
                    carrier=self.name,
                    status=status,
                    events=sorted_events,
                ))

        return results

    def _parse_tracking_text(self, text: str) -> list[TrackingEvent]:
        """Parse tracking events from plain text block."""
        events: list[TrackingEvent] = []

        # Clean SSR whitespace: replace literal \n, strip lines
        text = text.replace("\\n", "\n")
        lines = [line.strip() for line in text.split("\n")]
        normalized = "\n".join(lines)
        # Collapse 2+ blank lines into one
        normalized = re.sub(r"\n{2,}", "\n\n", normalized)
        # Rejoin "DD.MM.YYYY\n,\nHH:MM" into one line
        normalized = re.sub(
            r"(\d{2}\.\d{2}\.\d{4})\n+,\n+(\d{2}:\d{2})",
            r"\1, \2",
            normalized,
        )

        # Pattern: "DD.MM.YYYY, HH:MM\n...\nDescription"
        # Capture date/time, then everything until the description line
        pattern = r"(\d{2}\.\d{2}\.\d{4}),?\s*(\d{2}:\d{2})?\n+([^\n]*)\n+([^\n]+)"
        for match in re.finditer(pattern, normalized):
            date_str, time_str, location, description = match.groups()
            time_str = time_str or "00:00"
            try:
                ts = datetime.strptime(
                    f"{date_str} {time_str}", "%d.%m.%Y %H:%M"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            events.append(TrackingEvent(
                timestamp=ts,
                status=_parse_status(description),
                description=description.strip(),
                location=location.strip() or None,
            ))

        return events

    def _parse_event_text(self, text: str) -> TrackingEvent | None:
        """Parse a single tracking event from text."""
        match = re.match(
            r"(\d{2}\.\d{2}\.\d{4})\s*,?\s*(\d{2}:\d{2})\s*(.*)",
            text, re.DOTALL,
        )
        if not match:
            return None

        date_str, time_str, rest = match.groups()
        try:
            ts = datetime.strptime(
                f"{date_str} {time_str}", "%d.%m.%Y %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        lines = [l.strip() for l in rest.strip().split("\n") if l.strip()]
        location = lines[0] if lines else None
        description = lines[1] if len(lines) > 1 else lines[0] if lines else ""

        return TrackingEvent(
            timestamp=ts,
            status=_parse_status(description),
            description=description,
            location=location if location != description else None,
        )

    def _parse_tracking_page(self, tracking_number: str, html: str) -> TrackingResult:
        soup = BeautifulSoup(html, "lxml")
        events: list[TrackingEvent] = []
        status = TrackingStatus.UNKNOWN

        status_el = soup.select_one(".status-info, .parcelStatus, [data-status]")
        if status_el:
            status = _parse_status(status_el.get_text(strip=True))

        for row in soup.select(
            ".parcel-event, .tracking-event, tr.event, .statusList li"
        ):
            date_el = row.select_one(".date, .event-date, td:nth-child(1)")
            desc_el = row.select_one(".description, .event-description, td:nth-child(2)")
            loc_el = row.select_one(".location, .event-location, td:nth-child(3)")

            description = desc_el.get_text(strip=True) if desc_el else ""
            location = loc_el.get_text(strip=True) if loc_el else None
            date_text = date_el.get_text(strip=True) if date_el else ""

            try:
                ts = datetime.fromisoformat(date_text) if date_text else datetime.now(timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)

            if description:
                events.append(TrackingEvent(
                    timestamp=_ensure_utc(ts),
                    status=_parse_status(description),
                    description=description,
                    location=location or None,
                ))

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
