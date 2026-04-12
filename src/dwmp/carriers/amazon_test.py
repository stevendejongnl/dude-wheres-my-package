import json
import pytest
from datetime import datetime, timezone
from dwmp.carriers.base import AuthTokens, AuthType, CarrierAuthError, TrackingStatus
from dwmp.carriers.amazon import (
    Amazon,
    _parse_cookies,
    _parse_dutch_date,
    _parse_status,
)


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
    assert dt == datetime(2026, 4, 5, tzinfo=timezone.utc)


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
