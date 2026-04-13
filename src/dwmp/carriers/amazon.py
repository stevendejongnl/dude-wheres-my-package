import json
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
AMAZON_ORDERS_URL = f"{AMAZON_BASE}/your-orders/orders"
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
    auth_type = AuthType.CREDENTIALS

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client
        self._updated_tokens: AuthTokens | None = None

    def get_updated_tokens(self) -> AuthTokens | None:
        """Return refreshed browser cookies captured during the last sync."""
        tokens = self._updated_tokens
        self._updated_tokens = None
        return tokens

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        totp_secret = kwargs.get("totp_secret")

        _html, cookies_json = await _playwright_login_and_capture(
            email=username,
            password=password,
            totp_secret=totp_secret,
            orders_url=AMAZON_ORDERS_URL,
        )

        # Store credentials for automatic re-login when cookies expire
        creds = json.dumps({
            "email": username,
            "password": password,
            "totp_secret": totp_secret,
        })

        return AuthTokens(
            access_token=cookies_json,
            refresh_token=creds,
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raw = tokens.access_token
        self._updated_tokens = None

        # Mode 1: Playwright cookies (JSON array) → automated browser capture
        if raw and raw.strip().startswith("["):
            from dwmp.carriers.browser import capture_page_html

            try:
                html, updated_cookies = await capture_page_html(
                    url=AMAZON_ORDERS_URL,
                    cookies_json=raw,
                    carrier_name=self.name,
                    login_indicators=["ap/signin", "ap/mfa"],
                    wait_selector=".order-card, .js-order-card, .a-box-group.order",
                )
                self._updated_tokens = AuthTokens(
                    access_token=updated_cookies,
                    refresh_token=tokens.refresh_token,
                )
            except CarrierAuthError:
                # Cookies expired — re-login with stored credentials
                logger.info("Amazon cookies expired, attempting re-login")
                html = await self._relogin(tokens)

            return self._parse_orders_page(html, lookback_days)

        # Mode 2: Legacy raw HTML (manual capture, backwards compatible)
        if raw and raw.strip().startswith("<"):
            return self._parse_orders_page(raw, lookback_days)

        # Mode 3: No cookies yet — first sync after login() stored credentials
        if tokens.refresh_token:
            logger.info("No cached cookies, performing initial Amazon login")
            html = await self._relogin(tokens)
            return self._parse_orders_page(html, lookback_days)

        raise CarrierAuthError(
            carrier=self.name,
            message=(
                "Amazon account not configured. "
                "Connect with your Amazon email and password."
            ),
        )

    async def _relogin(self, tokens: AuthTokens) -> str:
        """Re-login using stored credentials. Returns orders page HTML."""
        if not tokens.refresh_token:
            raise CarrierAuthError(
                self.name,
                "Session expired and no stored credentials for re-login. "
                "Reconnect your Amazon account.",
            )

        creds = json.loads(tokens.refresh_token)
        html, updated_cookies = await _playwright_login_and_capture(
            email=creds["email"],
            password=creds["password"],
            totp_secret=creds.get("totp_secret"),
            orders_url=AMAZON_ORDERS_URL,
        )
        self._updated_tokens = AuthTokens(
            access_token=updated_cookies,
            refresh_token=tokens.refresh_token,
        )
        return html

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        """Public tracking via the `share` endpoint.

        Amazon has no public (tracking_number → status) lookup — the order ID
        alone isn't a carrier-portal tracking number. But the orders page
        exposes a per-parcel shareable URL that renders without login. Sync
        captures that URL as `tracking_url`; this method fetches it.

        If no `tracking_url` is available we return UNKNOWN with no events —
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

        # The primary status headline lives in #primaryStatus or a similar
        # wrapper. Fall back to scanning the whole body for known phrases.
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
            ".milestone-list li, .pt-tracking-event, [data-testid='tracking-event']"
        ):
            text = item.get_text(" ", strip=True)
            if not text:
                continue
            events.append(TrackingEvent(
                timestamp=datetime.now(UTC),
                status=_parse_status(text),
                description=text,
            ))

        if not events and status != TrackingStatus.UNKNOWN:
            events.append(TrackingEvent(
                timestamp=datetime.now(UTC),
                status=status,
                description=status_text or status.value,
            ))

        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=status,
            events=events,
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

    Matches both `/progress-tracker/package/share?...` (same-host) and the
    `amzn.eu/d/...` short links (which 301 to the same tracker). Returns the
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


async def _playwright_login_and_capture(
    email: str,
    password: str,
    totp_secret: str | None,
    orders_url: str,
) -> tuple[str, str]:
    """Log in to Amazon via Playwright and capture the orders page.

    Navigates to the orders URL (Amazon redirects to sign-in if needed),
    authenticates, handles TOTP MFA, then captures the rendered HTML.

    Returns ``(orders_html, cookies_json)``.
    """
    from playwright.async_api import async_playwright

    from dwmp.carriers.browser import (
        _USER_AGENT,
        _browser_lock,
        _launch_browser,
        _stealth,
    )

    async with _browser_lock:
        async with _stealth(_USER_AGENT).use_async(async_playwright()) as pw:
            browser = await _launch_browser(pw)
            try:
                context = await browser.new_context(
                    user_agent=_USER_AGENT,
                    viewport={"width": 1280, "height": 800},
                    locale="nl-NL",
                    timezone_id="Europe/Amsterdam",
                )
                page = await context.new_page()

                # Navigate to orders — Amazon redirects to sign-in if needed
                await page.goto(orders_url, wait_until="networkidle", timeout=20_000)

                # Detect login page and authenticate
                if "ap/signin" in page.url or await page.query_selector("#ap_email"):
                    await _do_login(page, email, password, totp_secret)

                # Wait for orders to render
                try:
                    await page.wait_for_selector(
                        ".order-card, .js-order-card, .a-box-group.order",
                        timeout=15_000,
                    )
                except Exception:
                    logger.warning("Order cards not found — capturing page as-is")

                html = await page.content()
                cookies = await context.cookies()

                logger.info(
                    "Amazon login+capture complete, %d bytes, %d cookies",
                    len(html), len(cookies),
                )
                return html, json.dumps(cookies)
            finally:
                await browser.close()


async def _do_login(
    page: object,
    email: str,
    password: str,
    totp_secret: str | None,
) -> None:
    """Fill in Amazon's sign-in form (email → password → optional TOTP)."""
    # Email field
    email_input = await page.wait_for_selector(  # type: ignore[union-attr]
        "#ap_email", timeout=10_000
    )
    await email_input.fill(email)  # type: ignore[union-attr]

    # Some pages show email+password together, others split them
    password_input = await page.query_selector("#ap_password")  # type: ignore[union-attr]
    if password_input:
        await password_input.fill(password)
        await page.click("#signInSubmit")  # type: ignore[union-attr]
    else:
        await page.click("#continue")  # type: ignore[union-attr]
        await page.wait_for_selector("#ap_password", timeout=10_000)  # type: ignore[union-attr]
        await page.fill("#ap_password", password)  # type: ignore[union-attr]
        await page.click("#signInSubmit")  # type: ignore[union-attr]

    await page.wait_for_load_state("networkidle")  # type: ignore[union-attr]

    # Handle TOTP MFA
    mfa_input = await page.query_selector("#auth-mfa-otpcode")  # type: ignore[union-attr]
    if mfa_input:
        if not totp_secret:
            raise CarrierAuthError(
                "amazon",
                "Amazon MFA is enabled. Reconnect your account with your "
                "TOTP secret (the setup key from your authenticator app).",
            )
        import pyotp

        code = pyotp.TOTP(totp_secret).now()
        await mfa_input.fill(code)

        remember = await page.query_selector(  # type: ignore[union-attr]
            "#auth-mfa-remember-device"
        )
        if remember:
            await remember.check()

        submit = await page.query_selector(  # type: ignore[union-attr]
            "#auth-signin-button"
        )
        if submit:
            await submit.click()
        await page.wait_for_load_state("networkidle")  # type: ignore[union-attr]

    # Handle approval-based MFA (can't automate)
    if await page.query_selector(  # type: ignore[union-attr]
        "#auth-approve-notification, .cvf-widget-btn-verify"
    ):
        raise CarrierAuthError(
            "amazon",
            "Amazon is requesting push notification approval. "
            "Switch to TOTP-based MFA in your Amazon security settings "
            "for automated tracking to work.",
        )

    # Check if still on login page (wrong credentials)
    current_url = getattr(page, "url", "")
    if "ap/signin" in current_url:
        raise CarrierAuthError(
            "amazon",
            "Login failed — check your email and password.",
        )


class _noop_ctx:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        pass
