"""Amazon orders carrier — browser-push only.

The server never talks to Amazon directly. The DWMP Chrome extension logs
in on the user's own browser using the credentials stored on the account,
scrapes the orders page, and POSTs the HTML back via ``/browser-push``.
This module just parses that HTML.

The public share-tracker (``track()``) remains server-side — Amazon exposes
a per-parcel URL on the orders page that renders without login, and we
persist it as ``tracking_url`` when we first see it, so subsequent
refreshes can follow that link anonymously.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

logger = logging.getLogger(__name__)

AMAZON_BASE = "https://www.amazon.nl"
# Public share-tracking endpoint. Accessible without login when called with
# `unauthenticated=1&shareToken=<tok>`. The short-link domain `amzn.eu/d/<id>`
# 301s here. We extract either form during sync and store it as tracking_url.
AMAZON_SHARE_TRACKER_PREFIX = f"{AMAZON_BASE}/progress-tracker/package/share"

# Links on the orders page that surface the public shareable tracker.
# Matches both the short domain and the full tracker path.
_SHARE_LINK_PATTERNS = (
    "progress-tracker/package/share",
    "amzn.eu/d/",
)

# Dutch and English status texts from Amazon order pages.
# Ordered most-specific first to avoid partial matches
# (e.g. "wordt vandaag bezorgd" before "bezorgd").
STATUS_MAP: list[tuple[str, TrackingStatus]] = [
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
            # "Wordt morgen bezorgd" / "Wordt 14 apr. bezorgd" is future tense,
            # not a completed delivery.  "Wordt vandaag bezorgd" already matched
            # OUT_FOR_DELIVERY above, so this only catches the remaining cases.
            if status == TrackingStatus.DELIVERED and "wordt" in lower:
                return TrackingStatus.IN_TRANSIT
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
    year = int(year_str) if year_str else datetime.now(UTC).year

    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


class Amazon(CarrierBase):
    name = "amazon"
    auth_type = AuthType.BROWSER_PUSH

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client

    async def validate_token(self, tokens: AuthTokens) -> None:
        """No-op for browser-push carriers.

        There's nothing for the server to validate — the extension will
        exercise the credentials on the user's browser the first time it
        runs. Overriding the base default avoids calling ``sync_packages``
        which raises by design.
        """
        return

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        """Never called by the service layer for browser-push carriers.

        The tracking service early-returns for ``auth_type == BROWSER_PUSH``
        accounts. This override only exists to satisfy :class:`CarrierBase`'s
        abstract contract — if it *does* fire, something is misconfigured,
        so we fail loudly with a user-facing message rather than silently
        returning ``[]``.
        """
        raise CarrierAuthError(
            self.name,
            "Amazon syncs only via the DWMP Chrome extension. Install the "
            "extension and trigger a sync from its popup.",
        )

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        """Public tracking via the ``share`` endpoint.

        Amazon has no public (tracking_number → status) lookup — the order ID
        alone isn't a carrier-portal tracking number. But the orders page
        exposes a per-parcel shareable URL that renders without login. Sync
        captures that URL as ``tracking_url``; this method fetches it.

        If no ``tracking_url`` is available we return UNKNOWN with no events —
        the TrackingService downgrade safeguard preserves any prior status.
        """
        tracking_url = kwargs.get("tracking_url") or ""
        if not tracking_url:
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        async with self._get_client() as client:
            try:
                response = await client.get(
                    tracking_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                        ),
                        "Accept": "text/html",
                        "Accept-Language": "nl-NL,nl;q=0.9",
                    },
                    follow_redirects=True,
                    timeout=15,
                )
            except httpx.HTTPError as exc:
                logger.debug("Amazon share-track fetch failed: %s", exc)
                return TrackingResult(
                    tracking_number=tracking_number,
                    carrier=self.name,
                    status=TrackingStatus.UNKNOWN,
                )

        if response.status_code != 200:
            logger.debug(
                "Amazon share-track returned %s for %s",
                response.status_code, tracking_url,
            )
            return TrackingResult(
                tracking_number=tracking_number,
                carrier=self.name,
                status=TrackingStatus.UNKNOWN,
            )

        return self._parse_share_tracker(tracking_number, response.text)

    def _parse_share_tracker(
        self, tracking_number: str, html: str
    ) -> TrackingResult:
        """Parse the public progress-tracker share page.

        The page is server-rendered enough that the primary status line is
        present in the initial HTML response. Status copy is the same Dutch
        phrasing as the authenticated orders page, so STATUS_MAP still applies.
        """
        soup = BeautifulSoup(html, "lxml")

        status_el = soup.select_one(
            "#primaryStatus, [data-testid='primary-status'], "
            ".pt-delivery-card-primary-status, .pt-primary-status"
        )
        status_text = status_el.get_text(" ", strip=True) if status_el else ""
        status = _parse_status(status_text) if status_text else TrackingStatus.UNKNOWN

        if status == TrackingStatus.UNKNOWN:
            body_text = soup.get_text(" ", strip=True)
            status = _parse_status(body_text)
            if not status_text:
                status_text = body_text[:200]

        events: list[TrackingEvent] = []
        for item in soup.select(
            ".milestone-list li, .pt-tracking-event, "
            "[data-testid='tracking-event']"
        ):
            text = item.get_text(" ", strip=True)
            if not text:
                continue
            evt_status = _parse_status(text)
            evt_date = _parse_dutch_date(text)
            events.append(TrackingEvent(
                timestamp=evt_date or datetime.now(UTC),
                status=evt_status,
                description=text,
            ))

        if not events and status != TrackingStatus.UNKNOWN:
            evt_date = _parse_dutch_date(status_text) if status_text else None
            events.append(TrackingEvent(
                timestamp=evt_date or datetime.now(UTC),
                status=status,
                description=status_text or status.value,
            ))

        estimated = None
        if status in (TrackingStatus.IN_TRANSIT, TrackingStatus.OUT_FOR_DELIVERY):
            estimated = _parse_dutch_date(status_text) if status_text else None

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

    def _parse_parcels_page(
        self, html: str, lookback_days: int = 30
    ) -> list[TrackingResult]:
        """Entry point for the browser-push HTML sync path.

        Alias kept for interface parity with DPD and the tracking service's
        ``sync_account_from_html``.
        """
        return self._parse_orders_page(html, lookback_days)

    def _parse_orders_page(
        self, html: str, lookback_days: int = 30
    ) -> list[TrackingResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[TrackingResult] = []
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

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

        # Harvest the public "Track package" share URL if Amazon exposes one
        # on this card. Present only for shipped/in-flight orders, missing for
        # digital / not-yet-shipped / delivered-long-ago orders — in those
        # cases we still persist the package with just the order ID and fall
        # back to sync-only status updates.
        tracking_url = _extract_share_url(card)

        # --- delivery status ---
        status = TrackingStatus.UNKNOWN
        status_text = ""

        # Amazon uses .delivery-box with nested text; also try specific elements
        delivery_el = card.select_one(  # type: ignore[union-attr]
            ".delivery-box__primary-text, "
            ".shipment-is-delivered, "
            "[data-component='deliveryMessage'], "
            ".a-color-success"
        )
        if not delivery_el:
            # Fallback: .delivery-box first child text
            delivery_box = card.select_one(".delivery-box")  # type: ignore[union-attr]
            if delivery_box:
                # Get first meaningful text from the delivery box
                for child in delivery_box.descendants:
                    if isinstance(child, str):
                        text = child.strip()
                        if text and _parse_status(text) != TrackingStatus.UNKNOWN:
                            delivery_el = None
                            status_text = text
                            status = _parse_status(text)
                            break
        if delivery_el:
            status_text = delivery_el.get_text(strip=True)
            status = _parse_status(status_text)

        if status == TrackingStatus.UNKNOWN:
            status = _parse_status(card_text)

        # --- events ---
        events: list[TrackingEvent] = []

        # Order date — look for the value next to "Bestelling geplaatst"
        order_date_el = card.select_one(  # type: ignore[union-attr]
            ".a-color-secondary.value, .order-date-text"
        )
        if not order_date_el:
            # Fallback: find date near "Bestelling geplaatst" label
            for el in card.select(".a-color-secondary"):  # type: ignore[union-attr]
                text = el.get_text(strip=True)
                if _parse_dutch_date(text):
                    order_date_el = el
                    break

        if order_date_el:
            order_date_text = order_date_el.get_text(strip=True)
            order_date = _parse_dutch_date(order_date_text)
            if order_date:
                events.append(TrackingEvent(
                    timestamp=order_date,
                    status=TrackingStatus.PRE_TRANSIT,
                    description=order_date_text,
                ))

        # Delivery/expected date from status text.
        # Always add a status event when we have a known status — even without
        # a parseable date. Amazon often shows "Onderweg" or "Verwacht donderdag"
        # with no machine-readable date; without a fallback timestamp the
        # timeline would only contain the order date.
        if status_text and status != TrackingStatus.UNKNOWN:
            event_date = _parse_dutch_date(status_text)
            events.append(TrackingEvent(
                timestamp=event_date or datetime.now(UTC),
                status=status,
                description=status_text,
            ))

        estimated = None
        if status in (TrackingStatus.IN_TRANSIT, TrackingStatus.OUT_FOR_DELIVERY):
            estimated = _parse_dutch_date(status_text) if status_text else None

        if not events and status != TrackingStatus.UNKNOWN:
            events.append(TrackingEvent(
                timestamp=datetime.now(UTC),
                status=status,
                description=status_text or status.value,
            ))

        return TrackingResult(
            tracking_number=order_id,
            carrier=self.name,
            status=status,
            estimated_delivery=estimated,
            events=sorted(events, key=lambda e: e.timestamp),
            tracking_url=tracking_url,
        )


def _extract_share_url(card: object) -> str | None:
    """Find a public Amazon share-tracking URL inside an order card.

    Matches both ``/progress-tracker/package/share?...`` (same-host) and the
    ``amzn.eu/d/...`` short links (which 301 to the same tracker). Returns the
    absolute URL or None if the card has no shareable tracking link.
    """
    for link in card.select("a[href]"):  # type: ignore[union-attr]
        href = link.get("href") or ""
        if not any(pattern in href for pattern in _SHARE_LINK_PATTERNS):
            continue
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"{AMAZON_BASE}{href}"
        return f"{AMAZON_BASE}/{href.lstrip('/')}"
    return None


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
