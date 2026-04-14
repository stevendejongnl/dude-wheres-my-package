import pytest

from dwmp.carriers.base import AuthTokens, AuthType, CarrierAuthError, TrackingStatus
from dwmp.carriers.dpd import DPD, _is_guest_page, _parse_status


def test_dpd_is_credentials():
    assert DPD().auth_type == AuthType.CREDENTIALS


def test_parse_status_delivered():
    assert _parse_status("delivered") == TrackingStatus.DELIVERED
    assert _parse_status("Afgeleverd") == TrackingStatus.DELIVERED


def test_parse_status_in_transit():
    assert _parse_status("in transit") == TrackingStatus.IN_TRANSIT
    assert _parse_status("arrived at our depot") == TrackingStatus.IN_TRANSIT
    assert _parse_status("transported to our next premises") == TrackingStatus.IN_TRANSIT


def test_parse_status_pre_transit():
    assert _parse_status("exchanging data internally") == TrackingStatus.PRE_TRANSIT


def test_parse_status_unknown():
    assert _parse_status("???") == TrackingStatus.UNKNOWN


def test_parse_parcels_page():
    carrier = DPD()
    html = """
    <html><body>
    <ul class="parcel-list">
        <li class="active">
            <a href="/nl/mydpd/my-parcels/incoming?parcelNumber=05222667810779">
                <span class="parcelAlias">Parcel from Ventilatieland.nl</span>
            </a>
        </li>
    </ul>
    <span class="parcelNumber">05222667810779</span>
    <div class="parcelStatusBox">
        <div class="status-icon transit"></div>
        <div class="status-text">Your parcel is on its way to the delivery depot.</div>
    </div>
    </body></html>
    """
    results = carrier._parse_parcels_page(html)
    assert len(results) == 1
    assert results[0].tracking_number == "05222667810779"
    assert results[0].status == TrackingStatus.IN_TRANSIT


def test_parse_tracking_text():
    carrier = DPD()
    # Simulate clean text (as produced by get_text(separator='\n', strip=True))
    text = (
        "Tracking details\n"
        "You are seeing the same order status information.\n"
        "11.04.2026, 03:49\n"
        "Oirschot, NL\n"
        "Your parcel arrived at our depot\n"
        "10.04.2026, 14:05\n"
        "\n"
        "We are exchanging data internally\n"
    )
    events = carrier._parse_tracking_text(text)
    assert len(events) == 2
    assert events[0].location == "Oirschot, NL"
    assert events[0].status == TrackingStatus.IN_TRANSIT
    assert events[1].status == TrackingStatus.PRE_TRANSIT


def test_parse_empty_page():
    carrier = DPD()
    results = carrier._parse_parcels_page("<html><body></body></html>")
    assert results == []


async def test_dpd_rejects_oauth():
    carrier = DPD()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")


async def test_track_returns_unknown_without_postal_code():
    """track() without postal_code → UNKNOWN (can't verify)."""
    carrier = DPD()
    result = await carrier.track("05222667810779")
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


async def test_track_uses_playwright_guest_flow(monkeypatch):
    """track() with postal_code → Playwright guest verification → parsed results."""
    detail_html = """
    <html><body>
    <a href="/nl/mydpd/my-parcels/incoming?parcelNumber=05222667810779">
        <span class="parcelAlias">Parcel from TestSender</span>
    </a>
    <span class="parcelNumber">05222667810779</span>
    <div class="parcelStatusBox">
        <div class="status-icon transit"></div>
    </div>
    </body></html>
    """
    calls: list[tuple[str, str]] = []

    async def fake_guest_track(tracking_number, postal_code):
        calls.append((tracking_number, postal_code))
        return detail_html

    monkeypatch.setattr(
        "dwmp.carriers.dpd._playwright_guest_track", fake_guest_track
    )

    carrier = DPD()
    result = await carrier.track("05222667810779", postal_code="1431RZ")
    assert calls == [("05222667810779", "1431RZ")]
    assert result.tracking_number == "05222667810779"
    assert result.carrier == "dpd"
    # Status parsed from .status-icon class
    assert result.status == TrackingStatus.IN_TRANSIT


async def test_sync_packages_cookies_mode_captures_live_html(monkeypatch):
    """Cookies JSON → Playwright re-captures the page and refreshes tokens."""
    carrier = DPD()
    captured_html = """
    <html><body>
      <a href="/nl/mydpd/my-parcels/incoming?parcelNumber=NEWPARCEL123">
        <span class="parcelAlias">Parcel from Fresh Sender</span>
      </a>
    </body></html>
    """
    capture_calls: list[dict] = []

    async def fake_capture(**kwargs):
        capture_calls.append(kwargs)
        return captured_html, '[{"name":"cf_clearance","value":"rotated"}]'

    monkeypatch.setattr("dwmp.carriers.browser.capture_page_html", fake_capture)

    tokens = AuthTokens(
        access_token='[{"name":"cf_clearance","value":"old"}]',
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X) SafariTest/1.0",
    )
    results = await carrier.sync_packages(tokens)

    assert len(capture_calls) == 1
    assert capture_calls[0]["carrier_name"] == "dpd"
    assert "parcelNumber" in capture_calls[0]["wait_selector"]
    # UA must be forwarded so the headless replay matches the issuing browser.
    assert capture_calls[0]["user_agent"] == tokens.user_agent
    assert len(results) == 1
    assert results[0].tracking_number == "NEWPARCEL123"

    refreshed = carrier.get_updated_tokens()
    assert refreshed is not None
    assert "rotated" in refreshed.access_token
    # UA survives the token rotation — next sync keeps matching the original browser.
    assert refreshed.user_agent == tokens.user_agent
    # Consumed on read — next call returns None.
    assert carrier.get_updated_tokens() is None


async def test_sync_packages_legacy_html_still_works():
    """Backwards compat: existing accounts with pasted HTML keep parsing locally."""
    carrier = DPD()
    html = (
        "<html><body>"
        "<a href='/nl/mydpd/my-parcels/incoming?parcelNumber=LEGACY999'>"
        "<span class='parcelAlias'>Parcel from Old Sender</span></a>"
        "</body></html>"
    )
    results = await carrier.sync_packages(AuthTokens(access_token=html))
    assert len(results) == 1
    assert results[0].tracking_number == "LEGACY999"
    # Legacy mode never sets updated tokens.
    assert carrier.get_updated_tokens() is None


async def test_sync_detects_guest_mode(monkeypatch):
    """Expired Keycloak session → guest page → re-login attempted
    → no stored credentials → CarrierAuthError."""
    guest_html = """
    <html><body>
    <div>Gast Particuliere klanten Nederlands English Mijn pakketten</div>
    <div>Inloggen/Registreren</div>
    <div>1 × Guest User Login</div>
    <div>Binnenkomend 0 Versturen en retourneren 0</div>
    <p>Maak een account aan of log in om al je pakketten op één plek
       op te slaan en te volgen.</p>
    </body></html>
    """

    async def fake_capture(**kwargs):
        return guest_html, '[{"name":"cf_clearance","value":"still-valid"}]'

    monkeypatch.setattr("dwmp.carriers.browser.capture_page_html", fake_capture)

    carrier = DPD()
    # No refresh_token → re-login will fail, surfacing the guest-mode error
    tokens = AuthTokens(
        access_token='[{"name":"cf_clearance","value":"old"}]',
        user_agent="Mozilla/5.0",
    )
    with pytest.raises(CarrierAuthError, match="guest mode"):
        await carrier.sync_packages(tokens)

    # Tokens should NOT be updated on auth failure.
    assert carrier.get_updated_tokens() is None


async def test_sync_relogin_on_guest_mode(monkeypatch):
    """Expired session + stored credentials → automatic re-login succeeds."""
    guest_html = """
    <html><body>
    <div>Inloggen/Registreren</div>
    <div>Guest User Login</div>
    </body></html>
    """

    async def fake_capture(**kwargs):
        return guest_html, '[{"name":"cf_clearance","value":"still-valid"}]'

    relogin_html = """
    <html><body>
    <a href="/nl/mydpd/my-parcels/incoming?parcelNumber=RELOGIN123">
        <span class="parcelAlias">Parcel from Fresh Login</span>
    </a>
    </body></html>
    """
    login_calls: list[dict] = []

    async def fake_keycloak_login(**kwargs):
        login_calls.append(kwargs)
        return relogin_html, '[{"name":"session","value":"fresh"}]'

    monkeypatch.setattr("dwmp.carriers.browser.capture_page_html", fake_capture)
    monkeypatch.setattr("dwmp.carriers.dpd._playwright_keycloak_login", fake_keycloak_login)

    import json
    carrier = DPD()
    tokens = AuthTokens(
        access_token='[{"name":"cf_clearance","value":"old"}]',
        refresh_token=json.dumps({"email": "user@test.com", "password": "pass123"}),
    )
    results = await carrier.sync_packages(tokens)
    assert len(results) == 1
    assert results[0].tracking_number == "RELOGIN123"
    assert len(login_calls) == 1
    assert login_calls[0]["email"] == "user@test.com"

    refreshed = carrier.get_updated_tokens()
    assert refreshed is not None
    assert "fresh" in refreshed.access_token


async def test_sync_initial_login_with_credentials(monkeypatch):
    """No cached cookies + stored credentials → initial Keycloak login."""
    login_html = """
    <html><body>
    <a href="/nl/mydpd/my-parcels/incoming?parcelNumber=INITIAL456">
        <span class="parcelAlias">Parcel from First Login</span>
    </a>
    </body></html>
    """
    login_calls: list[dict] = []

    async def fake_keycloak_login(**kwargs):
        login_calls.append(kwargs)
        return login_html, '[{"name":"session","value":"new"}]'

    monkeypatch.setattr("dwmp.carriers.dpd._playwright_keycloak_login", fake_keycloak_login)

    import json
    carrier = DPD()
    tokens = AuthTokens(
        access_token="",  # No cached cookies
        refresh_token=json.dumps({"email": "user@test.com", "password": "pass123"}),
    )
    results = await carrier.sync_packages(tokens)
    assert len(results) == 1
    assert results[0].tracking_number == "INITIAL456"
    assert len(login_calls) == 1

    refreshed = carrier.get_updated_tokens()
    assert refreshed is not None


def test_is_guest_page_positive():
    assert _is_guest_page("<html>Guest User Login</html>")
    assert _is_guest_page("<div>Inloggen/Registreren</div>")
    assert _is_guest_page("Maak een account aan of log in om je pakketten")


def test_is_guest_page_negative():
    assert not _is_guest_page("<html><a href='?parcelNumber=123'>Parcel</a></html>")
    assert not _is_guest_page("")


async def test_sync_packages_empty_token_raises_auth_error():
    carrier = DPD()
    with pytest.raises(CarrierAuthError):
        await carrier.sync_packages(AuthTokens(access_token=""))
