import re

import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

AMAZON_BASE = "https://www.amazon.nl"
AMAZON_ORDERS_URL = f"{AMAZON_BASE}/your-orders/orders"

# Dutch and English status texts from Amazon order pages.
# Ordered most-specific first to avoid partial matches
# (e.g. "wordt vandaag bezorgd" before "bezorgd").
STATUS_MAP: list[tuple[str, TrackingStatus]] = [
    # Most-specific phrases first to avoid partial substring matches.
    # "niet bezorgd" must precede "bezorgd", etc.
    ("wordt vandaag bezorgd", TrackingStatus.OUT_FOR_DELIVERY),
    ("vandaag verwacht", TrackingStatus.OUT_FOR_DELIVERY),
    ("out for delivery", TrackingStatus.OUT_FOR_DELIVERY),
    ("niet bezorgd", TrackingStatus.FAILED_ATTEMPT),
    ("mislukte bezorging", TrackingStatus.FAILED_ATTEMPT),
    ("delivery attempted", TrackingStatus.FAILED_ATTEMPT),
    ("bezorgd", TrackingStatus.DELIVERED),
    ("afgeleverd", TrackingStatus.DELIVERED),
    ("delivered", TrackingStatus.DELIVERED),
    ("wordt momenteel verzonden", TrackingStatus.PRE_TRANSIT),
    ("verzonden", TrackingStatus.IN_TRANSIT),
    ("onderweg", TrackingStatus.IN_TRANSIT),
    ("shipped", TrackingStatus.IN_TRANSIT),
    ("verwacht", TrackingStatus.IN_TRANSIT),
    ("arriving", TrackingStatus.IN_TRANSIT),
    ("besteld", TrackingStatus.PRE_TRANSIT),
    ("ordered", TrackingStatus.PRE_TRANSIT),
    ("teruggestuurd", TrackingStatus.RETURNED),
    ("retourgezonden", TrackingStatus.RETURNED),
    ("returned", TrackingStatus.RETURNED),
    ("geannuleerd", TrackingStatus.EXCEPTION),
    ("cancelled", TrackingStatus.EXCEPTION),
    ("geweigerd", TrackingStatus.EXCEPTION),
]

# Amazon order ID format: 305-1234567-8901234
ORDER_ID_PATTERN = re.compile(r"\d{3}-\d{7}-\d{7}")

DUTCH_MONTHS: dict[str, int] = {
    "jan": 1, "januari": 1,
    "feb": 2, "februari": 2,
    "mrt": 3, "maart": 3,
    "apr": 4, "april": 4,
    "mei": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "aug": 8, "augustus": 8,
    "sep": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

DUTCH_DATE_PATTERN = re.compile(
    r"(\d{1,2})\s+"
    r"(jan(?:uari)?|feb(?:ruari)?|mrt|maart|apr(?:il)?|mei|"
    r"jun(?:i)?|jul(?:i)?|aug(?:ustus)?|sep(?:tember)?|okt(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
    r"\.?\s*(\d{4})?",
    re.IGNORECASE,
)


def _parse_status(text: str) -> TrackingStatus:
    lower = text.lower()
    for key, status in STATUS_MAP:
        if key in lower:
            return status
    return TrackingStatus.UNKNOWN


def _parse_dutch_date(text: str) -> datetime | None:
    """Parse Dutch date text like '8 apr.' or '8 april 2026'."""
    match = DUTCH_DATE_PATTERN.search(text)
    if not match:
        return None
    day = int(match.group(1))
    month_str = match.group(2).lower().rstrip(".")
    year_str = match.group(3)

    month = DUTCH_MONTHS.get(month_str)
    if not month:
        return None
    year = int(year_str) if year_str else datetime.now(timezone.utc).year

    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_cookies(cookie_string: str) -> dict[str, str]:
    """Parse a raw Cookie header string into a dict."""
    cookies: dict[str, str] = {}
    for part in cookie_string.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            cookies[key.strip()] = value.strip()
    return cookies


class Amazon(CarrierBase):
    name = "amazon"
    auth_type = AuthType.MANUAL_TOKEN

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        cookies = _parse_cookies(tokens.access_token)
        if not cookies:
            raise CarrierAuthError(
                carrier=self.name,
                message=(
                    "Invalid cookie string. Copy the full Cookie header "
                    "from your browser's dev tools while on amazon.nl."
                ),
            )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        }

        time_filter = "months-6" if lookback_days > 90 else "months-3"

        async with self._get_client() as client:
            response = await client.get(
                AMAZON_ORDERS_URL,
                params={"timeFilter": time_filter, "startIndex": 0},
                headers=headers,
                cookies=cookies,
                follow_redirects=True,
            )

            if "ap/signin" in str(response.url) or response.status_code == 401:
                raise CarrierAuthError(
                    carrier=self.name,
                    message="Session expired. Capture fresh cookies from amazon.nl.",
                )

            response.raise_for_status()

        return self._parse_orders_page(response.text, lookback_days)

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        # Amazon has no public tracking endpoint.
        # Packages are tracked via account sync; individual tracking
        # should use the underlying carrier (PostNL, DHL, DPD).
        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=TrackingStatus.UNKNOWN,
        )

    def _get_client(self):
        if self._client:
            return _noop_ctx(self._client)
        return httpx.AsyncClient()

    def _parse_orders_page(
        self, html: str, lookback_days: int = 30
    ) -> list[TrackingResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[TrackingResult] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        order_cards = soup.select(
            ".order-card, .js-order-card, .a-box-group.order"
        )

        if not order_cards:
            order_cards = self._find_order_sections(soup)

        for card in order_cards:
            result = self._parse_order_card(card)
            if not result:
                continue
            if result.events:
                latest = max(e.timestamp for e in result.events)
                if latest < cutoff:
                    continue
            results.append(result)

        return results

    def _find_order_sections(self, soup: BeautifulSoup) -> list:
        """Fallback: find sections containing order IDs."""
        sections: list = []
        for el in soup.find_all(string=ORDER_ID_PATTERN):
            parent = el.find_parent(class_=re.compile(r"a-box|order|shipment"))
            if parent and parent not in sections:
                sections.append(parent)
        return sections

    def _parse_order_card(self, card: object) -> TrackingResult | None:
        """Parse a single order card from Amazon's order history HTML."""
        card_text = card.get_text(separator=" ", strip=True)  # type: ignore[union-attr]

        order_match = ORDER_ID_PATTERN.search(card_text)
        if not order_match:
            return None
        order_id = order_match.group()

        # --- delivery status ---
        status = TrackingStatus.UNKNOWN
        status_text = ""

        delivery_el = card.select_one(  # type: ignore[union-attr]
            ".delivery-box__primary-text, "
            ".shipment-is-delivered, "
            "[data-component='deliveryMessage'], "
            ".a-color-success, "
            ".delivery-box"
        )
        if delivery_el:
            status_text = delivery_el.get_text(strip=True)
            status = _parse_status(status_text)

        if status == TrackingStatus.UNKNOWN:
            status = _parse_status(card_text)

        # --- events ---
        events: list[TrackingEvent] = []

        # Order date
        order_date_el = card.select_one(  # type: ignore[union-attr]
            ".a-color-secondary, .order-date-text"
        )
        if order_date_el:
            order_date_text = order_date_el.get_text(strip=True)
            order_date = _parse_dutch_date(order_date_text)
            if order_date:
                events.append(TrackingEvent(
                    timestamp=order_date,
                    status=TrackingStatus.PRE_TRANSIT,
                    description=order_date_text,
                ))

        # Delivery/expected date from status text
        if status_text:
            event_date = _parse_dutch_date(status_text)
            if event_date:
                events.append(TrackingEvent(
                    timestamp=event_date,
                    status=status,
                    description=status_text,
                ))

        estimated = None
        if status in (TrackingStatus.IN_TRANSIT, TrackingStatus.OUT_FOR_DELIVERY):
            estimated = _parse_dutch_date(status_text) if status_text else None

        if not events and status != TrackingStatus.UNKNOWN:
            events.append(TrackingEvent(
                timestamp=datetime.now(timezone.utc),
                status=status,
                description=status_text or status.value,
            ))

        return TrackingResult(
            tracking_number=order_id,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=sorted(events, key=lambda e: e.timestamp),
        )


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
