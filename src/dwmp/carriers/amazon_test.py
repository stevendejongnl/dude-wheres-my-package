import json
from datetime import UTC, datetime

import pytest

from dwmp.carriers.amazon import (
    Amazon,
    _do_login,
    _parse_cookies,
    _parse_dutch_date,
    _parse_status,
)
from dwmp.carriers.base import AuthTokens, AuthType, CarrierAuthError, TrackingStatus


def test_amazon_is_credentials():
    assert Amazon().auth_type == AuthType.CREDENTIALS


# --- status parsing ---


def test_parse_status_delivered():
    assert _parse_status("Bezorgd op 8 apr.") == TrackingStatus.DELIVERED
    assert _parse_status("Afgeleverd") == TrackingStatus.DELIVERED
    assert _parse_status("Delivered") == TrackingStatus.DELIVERED


def test_parse_status_out_for_delivery():
    assert _parse_status("Wordt vandaag bezorgd") == TrackingStatus.OUT_FOR_DELIVERY
    assert _parse_status("Vandaag verwacht") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_in_transit():
    assert _parse_status("Verzonden") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Onderweg") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Verwacht op woensdag 16 april") == TrackingStatus.IN_TRANSIT


def test_parse_status_future_delivery_is_not_delivered():
    """'Wordt morgen bezorgd' is future tense — NOT delivered."""
    assert _parse_status("Wordt morgen bezorgd") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Wordt 14 apr. bezorgd") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Wordt dinsdag bezorgd") == TrackingStatus.IN_TRANSIT
    # But actual past-tense delivery is still DELIVERED
    assert _parse_status("Bezorgd op 8 apr.") == TrackingStatus.DELIVERED
    # And "vandaag" is the existing OUT_FOR_DELIVERY
    assert _parse_status("Wordt vandaag bezorgd") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_pre_transit():
    assert _parse_status("Besteld op 5 april 2026") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("Wordt momenteel verzonden") == TrackingStatus.PRE_TRANSIT


def test_parse_status_failed():
    assert _parse_status("Niet bezorgd") == TrackingStatus.FAILED_ATTEMPT
    assert _parse_status("Mislukte bezorging") == TrackingStatus.FAILED_ATTEMPT


def test_parse_status_returned():
    assert _parse_status("Teruggestuurd") == TrackingStatus.RETURNED
    assert _parse_status("Retourgezonden") == TrackingStatus.RETURNED


def test_parse_status_exception():
    assert _parse_status("Geannuleerd") == TrackingStatus.EXCEPTION


def test_parse_status_unknown():
    assert _parse_status("???") == TrackingStatus.UNKNOWN


# --- Dutch date parsing ---


def test_parse_dutch_date_short_month():
    dt = _parse_dutch_date("8 apr.")
    assert dt is not None
    assert dt.month == 4
    assert dt.day == 8


def test_parse_dutch_date_full_month():
    dt = _parse_dutch_date("5 april 2026")
    assert dt == datetime(2026, 4, 5, tzinfo=UTC)


def test_parse_dutch_date_in_sentence():
    dt = _parse_dutch_date("Bezorgd op 8 apr.")
    assert dt is not None
    assert dt.day == 8
    assert dt.month == 4


def test_parse_dutch_date_no_match():
    assert _parse_dutch_date("no date here") is None


# --- cookie parsing ---


def test_parse_cookies():
    raw = "session-id=abc; session-token=xyz; at-acbnl=secret"
    cookies = _parse_cookies(raw)
    assert cookies == {
        "session-id": "abc",
        "session-token": "xyz",
        "at-acbnl": "secret",
    }


def test_parse_cookies_empty():
    assert _parse_cookies("") == {}


# --- order page HTML parsing ---


def test_parse_orders_page_delivered():
    carrier = Amazon()
    html = """
    <html><body>
    <div class="order-card">
        <span class="a-color-secondary">Besteld op 5 april 2026</span>
        <span class="value">305-1234567-8901234</span>
        <div class="delivery-box">
            <span class="delivery-box__primary-text">Bezorgd op 8 apr.</span>
        </div>
        <a class="yohtmlc-product-title" href="/dp/B0TEST">USB-C Cable</a>
    </div>
    </body></html>
    """
    results = carrier._parse_orders_page(html)
    assert len(results) == 1
    assert results[0].tracking_number == "305-1234567-8901234"
    assert results[0].status == TrackingStatus.DELIVERED
    assert any(e.status == TrackingStatus.DELIVERED for e in results[0].events)


def test_parse_orders_page_delivered_via_delivery_box_text():
    """Amazon renders status as text node inside .delivery-box, not a child element."""
    carrier = Amazon()
    html = """
    <html><body>
    <div class="order-card">
        <div class="yohtmlc-order-id">
            Bestelnummer
            403-4691614-9201953
        </div>
        <div class="a-box delivery-box">
            Bezorgd op 1 april
        </div>
    </div>
    </body></html>
    """
    results = carrier._parse_orders_page(html)
    assert len(results) == 1
    assert results[0].tracking_number == "403-4691614-9201953"
    assert results[0].status == TrackingStatus.DELIVERED


def test_parse_orders_page_in_transit():
    carrier = Amazon()
    html = """
    <html><body>
    <div class="order-card">
        <span class="a-color-secondary">Besteld op 10 april 2026</span>
        <span class="value">305-9876543-2109876</span>
        <div class="delivery-box">
            <span class="delivery-box__primary-text">Verwacht op 16 april</span>
        </div>
    </div>
    </body></html>
    """
    results = carrier._parse_orders_page(html)
    assert len(results) == 1
    assert results[0].status == TrackingStatus.IN_TRANSIT
    assert results[0].estimated_delivery is not None
    assert results[0].estimated_delivery.day == 16


async def test_sync_rejects_unconfigured():
    """No cookies, no HTML, no credentials → clear error."""
    carrier = Amazon()
    tokens = AuthTokens(access_token="session-id=abc; session-token=xyz")
    with pytest.raises(CarrierAuthError, match="not configured"):
        await carrier.sync_packages(tokens)


async def test_sync_detects_cookies_json(monkeypatch):
    """When access_token is a JSON array, sync uses browser automation."""
    captured_args: dict = {}

    async def fake_capture(url, cookies_json, carrier_name, **kw):
        captured_args["url"] = url
        captured_args["cookies_json"] = cookies_json
        html = """
        <html><body>
        <div class="order-card">
            <span>305-0000000-0000000</span>
            <span class="a-color-success">Bezorgd</span>
        </div>
        </body></html>
        """
        return html, '[{"name":"refreshed","value":"1"}]'

    monkeypatch.setattr(
        "dwmp.carriers.browser.capture_page_html", fake_capture
    )

    carrier = Amazon()
    cookies = '[{"name":"session-id","value":"abc","domain":".amazon.nl"}]'
    tokens = AuthTokens(access_token=cookies, refresh_token='{"email":"x","password":"y"}')
    results = await carrier.sync_packages(tokens)

    assert len(results) == 1
    assert results[0].tracking_number == "305-0000000-0000000"
    assert captured_args["url"] == "https://www.amazon.nl/your-orders/orders"

    # Updated tokens should preserve credentials in refresh_token
    updated = carrier.get_updated_tokens()
    assert updated is not None
    assert "refreshed" in updated.access_token
    assert "email" in updated.refresh_token


async def test_sync_auto_relogins_on_expired_cookies(monkeypatch):
    """When cookies expire (CarrierAuthError), auto-re-login with stored credentials."""
    login_called = []

    async def fake_capture_expired(**kw):
        raise CarrierAuthError("amazon", "Session expired")

    async def fake_login(email, password, totp_secret, orders_url):
        login_called.append(email)
        html = """
        <html><body>
        <div class="order-card">
            <span>305-1111111-1111111</span>
            <span class="a-color-success">Bezorgd</span>
        </div>
        </body></html>
        """
        return html, '[{"name":"fresh","value":"1"}]'

    monkeypatch.setattr(
        "dwmp.carriers.browser.capture_page_html", fake_capture_expired
    )
    monkeypatch.setattr(
        "dwmp.carriers.amazon._playwright_login_and_capture", fake_login
    )

    carrier = Amazon()
    creds = json.dumps({"email": "test@example.com", "password": "secret"})
    tokens = AuthTokens(access_token='[{"name":"old"}]', refresh_token=creds)
    results = await carrier.sync_packages(tokens)

    assert len(results) == 1
    assert login_called == ["test@example.com"]
    updated = carrier.get_updated_tokens()
    assert updated is not None
    assert "fresh" in updated.access_token


async def test_sync_relogin_fails_without_credentials(monkeypatch):
    """If cookies expire and no credentials stored, raise clear error."""
    async def fake_capture_expired(**kw):
        raise CarrierAuthError("amazon", "Session expired")

    monkeypatch.setattr(
        "dwmp.carriers.browser.capture_page_html", fake_capture_expired
    )

    carrier = Amazon()
    tokens = AuthTokens(access_token='[{"name":"old"}]', refresh_token=None)
    with pytest.raises(CarrierAuthError, match="no stored credentials"):
        await carrier.sync_packages(tokens)


async def test_login_stores_credentials(monkeypatch):
    """login() should return cookies in access_token and credentials in refresh_token."""
    async def fake_login(email, password, totp_secret, orders_url):
        return "<html></html>", '[{"name":"session","value":"abc"}]'

    monkeypatch.setattr(
        "dwmp.carriers.amazon._playwright_login_and_capture", fake_login
    )

    carrier = Amazon()
    tokens = await carrier.login("user@example.com", "pass123", totp_secret="JBSWY3DP")

    assert tokens.access_token.startswith("[")
    creds = json.loads(tokens.refresh_token)
    assert creds["email"] == "user@example.com"
    assert creds["password"] == "pass123"
    assert creds["totp_secret"] == "JBSWY3DP"


def test_get_updated_tokens_returns_none_by_default():
    carrier = Amazon()
    assert carrier.get_updated_tokens() is None


async def test_legacy_html_mode_still_works():
    """Raw HTML in access_token should still parse (backwards compatible)."""
    carrier = Amazon()
    html = """
    <html><body>
    <div class="order-card">
        <span>305-9999999-9999999</span>
        <span class="a-color-success">Bezorgd</span>
    </div>
    </body></html>
    """
    tokens = AuthTokens(access_token=html)
    results = await carrier.sync_packages(tokens)
    assert len(results) == 1
    assert results[0].tracking_number == "305-9999999-9999999"
    assert carrier.get_updated_tokens() is None


def test_parse_orders_page_empty():
    carrier = Amazon()
    results = carrier._parse_orders_page("<html><body></body></html>")
    assert results == []


def test_parse_orders_page_fallback_to_text_scan():
    """When no .order-card class exists, fall back to finding order IDs."""
    carrier = Amazon()
    html = """
    <html><body>
    <div class="a-box">
        <span>305-1111111-2222222</span>
        <span class="a-color-success">Bezorgd</span>
    </div>
    </body></html>
    """
    results = carrier._parse_orders_page(html)
    assert len(results) == 1
    assert results[0].tracking_number == "305-1111111-2222222"
    assert results[0].status == TrackingStatus.DELIVERED


async def test_track_returns_unknown():
    """Amazon has no public tracking — track() always returns UNKNOWN."""
    carrier = Amazon()
    result = await carrier.track("305-1234567-8901234")
    assert result.status == TrackingStatus.UNKNOWN


async def test_amazon_rejects_oauth():
    carrier = Amazon()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")


# --- _do_login branch coverage ---
#
# Playwright's real ``Page`` is far too heavy to stub; these tests exercise the
# branching logic with a minimal fake that records actions so we can assert
# the right path is taken for each of Amazon's three entry points.


class _FakeElement:
    def __init__(self, element_id: str, visible: bool = True):
        self.id = element_id
        self._visible = visible
        self.filled: str | None = None

    async def fill(self, value: str) -> None:
        self.filled = value

    async def is_visible(self) -> bool:
        return self._visible

    async def evaluate(self, _script: str) -> str:
        # Only used for ``el => el.id`` in production code.
        return self.id


class _FakePage:
    """Minimal stand-in for playwright.async_api.Page covering the selectors
    ``_do_login`` actually queries. Selector handling is *string-based* — it
    splits a comma-separated CSS selector and returns the first configured
    element it finds. Good enough for branching assertions, nowhere near a
    full CSS engine."""

    def __init__(
        self,
        elements: dict[str, _FakeElement],
        url: str = "https://www.amazon.nl/ap/signin",
    ):
        self._elements = elements
        self.url = url
        self.actions: list[str] = []

    def _lookup(self, selector: str) -> _FakeElement | None:
        for sel in (s.strip() for s in selector.split(",")):
            if not sel.startswith("#"):
                continue
            el = self._elements.get(sel[1:])
            if el is not None:
                return el
        return None

    async def wait_for_selector(
        self, selector: str, timeout: int = 0, state: str = "visible",
    ) -> _FakeElement:
        el = self._lookup(selector)
        if el is None or (state == "visible" and not el._visible):
            from playwright.async_api import TimeoutError as PWTimeout
            raise PWTimeout(f"no element matched {selector!r}")
        return el

    async def query_selector(self, selector: str) -> _FakeElement | None:
        return self._lookup(selector)

    async def click(self, selector: str) -> None:
        self.actions.append(f"click:{selector}")
        # Submitting navigates away from the sign-in URL — the real-page
        # equivalent that the "still on /ap/signin" tail-check relies on.
        if selector in ("#signInSubmit", "#auth-signin-button"):
            self.url = "https://www.amazon.nl/your-orders/orders"

    async def fill(self, selector: str, value: str) -> None:
        el = self._lookup(selector)
        if el is not None:
            el.filled = value
        self.actions.append(f"fill:{selector}={value}")

    async def wait_for_load_state(self, _state: str) -> None:
        pass


async def test_do_login_recognized_user_password_only_flow():
    """Amazon skips the email step for recognized users — only #ap_password shows."""
    password_el = _FakeElement("ap_password")
    page = _FakePage({"ap_password": password_el})

    await _do_login(page, "user@example.com", "hunter2", totp_secret=None)

    assert password_el.filled == "hunter2"
    assert "click:#signInSubmit" in page.actions


async def test_do_login_fresh_sign_in_same_page():
    """Email + password visible on the same page (legacy flow)."""
    email_el = _FakeElement("ap_email")
    password_el = _FakeElement("ap_password")
    page = _FakePage({"ap_email": email_el, "ap_password": password_el})

    await _do_login(page, "user@example.com", "hunter2", totp_secret=None)

    assert email_el.filled == "user@example.com"
    assert password_el.filled == "hunter2"
    assert "click:#signInSubmit" in page.actions
    assert "click:#continue" not in page.actions  # no split step needed


async def test_do_login_fresh_sign_in_split_flow():
    """Email appears first; #ap_password only shows after clicking Continue."""
    email_el = _FakeElement("ap_email")
    # Password field exists but hidden initially — appears after the #continue click.
    password_el = _FakeElement("ap_password", visible=False)
    page = _FakePage({"ap_email": email_el, "ap_password": password_el})

    # Simulate the password field becoming visible mid-flow, which is what
    # Amazon does after #continue is clicked.
    original_click = page.click

    async def click_with_reveal(selector: str) -> None:
        await original_click(selector)
        if selector == "#continue":
            password_el._visible = True

    page.click = click_with_reveal  # type: ignore[method-assign]

    await _do_login(page, "user@example.com", "hunter2", totp_secret=None)

    assert email_el.filled == "user@example.com"
    assert page.actions.count("click:#continue") == 1
    # fill() via page.fill (not element.fill) records an action, so check both.
    assert any(a.startswith("fill:#ap_password=") for a in page.actions)
    assert page.actions[-1] == "click:#signInSubmit"


async def test_do_login_no_form_raises_clear_error():
    """No known form fields → descriptive CarrierAuthError, not a raw timeout."""
    page = _FakePage({}, url="https://www.amazon.nl/ap/bot-challenge")

    with pytest.raises(CarrierAuthError, match="login form did not appear"):
        await _do_login(page, "user@example.com", "hunter2", totp_secret=None)


async def test_do_login_captcha_raises_specific_error():
    """CAPTCHA presence → actionable error, not a generic timeout."""
    page = _FakePage({"auth-captcha-guess": _FakeElement("auth-captcha-guess")})

    with pytest.raises(CarrierAuthError, match="CAPTCHA"):
        await _do_login(page, "user@example.com", "hunter2", totp_secret=None)
