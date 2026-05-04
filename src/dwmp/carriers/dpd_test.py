import pytest

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierSyncError,
    TrackingStatus,
)
from dwmp.carriers.dpd import DPD, _is_error_page, _is_guest_page, _parse_status


def test_dpd_is_browser_push():
    assert DPD().auth_type == AuthType.BROWSER_PUSH


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


# --- parcels page HTML parsing (extension-pushed) ---


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


def test_parse_parcels_page_real_dom():
    """Parse the real DPD DOM structure with .content-item-track timeline,
    sender from delivery address, and postal code."""
    carrier = DPD()
    html = """
    <html><body>
    <a href="/nl/mydpd/my-parcels/incoming?parcelNumber=05222667810779">
        <span class="parcelAlias">Parcel from Ventilatieland.nl</span>
    </a>
    <span class="parcelNumber">05222667810779</span>
    <div class="parcelStatusBox">
        <div class="status-icon transit"></div>
    </div>

    <ul class="content-holder-track">
        <li class="content-item-track">
            <div class="timeline-entry">
                <div class="entry-header">
                    <div class="time-track">
                        <span class="entry-date">13.04.2026</span>
                        <span>, </span>
                        <span class="entry-time">04:49</span>
                    </div>
                    <div class="place-track"><span>Oirschot, NL</span></div>
                </div>
                <div class="entry-body"><p>Your parcel is on its way</p></div>
            </div>
        </li>
        <li class="content-item-track">
            <div class="timeline-entry">
                <div class="entry-header">
                    <div class="time-track">
                        <span class="entry-date">11.04.2026</span>
                        <span>, </span>
                        <span class="entry-time">03:49</span>
                    </div>
                    <div class="place-track"><span>Oirschot, NL</span></div>
                </div>
                <div class="entry-body"><p>Your parcel arrived at our depot</p></div>
            </div>
        </li>
        <li class="content-item-track last">
            <div class="timeline-entry">
                <div class="entry-header">
                    <div class="time-track">
                        <span class="entry-date">10.04.2026</span>
                        <span>, </span>
                        <span class="entry-time">14:05</span>
                    </div>
                    <div class="place-track"><span></span></div>
                </div>
                <div class="entry-body"><p>We are exchanging data internally</p></div>
            </div>
        </li>
    </ul>

    <div class="deliveryDetails">
        <div class="block-data">
            <p class="block-data-label">From:</p>
            <p>Ventilatieland.nl</p>
        </div>
        <div>
            <p class="delivery-address-icon location"></p>
            <p>Cyclamenstraat 55 , 1431RZ Aalsmeer</p>
        </div>
    </div>
    </body></html>
    """
    results = carrier._parse_parcels_page(html)
    assert len(results) == 1
    r = results[0]
    assert r.tracking_number == "05222667810779"
    assert r.status == TrackingStatus.IN_TRANSIT
    assert r.postal_code == "1431RZ"

    # 3 timeline events + 1 sender PRE_TRANSIT
    descriptions = [e.description for e in r.events]
    assert "Ventilatieland.nl" in descriptions
    assert "Your parcel is on its way" in descriptions
    assert "Your parcel arrived at our depot" in descriptions
    assert "We are exchanging data internally" in descriptions

    # Verify locations are parsed
    transit_events = [e for e in r.events if e.location]
    assert any(e.location == "Oirschot, NL" for e in transit_events)

    # Verify chronological order
    timestamps = [e.timestamp for e in r.events]
    assert timestamps == sorted(timestamps)


def test_parse_empty_page():
    carrier = DPD()
    results = carrier._parse_parcels_page("<html><body></body></html>")
    assert results == []


# --- sync / auth contracts ---


async def test_sync_packages_raises_because_browser_push_only():
    """sync_packages is never called for BROWSER_PUSH carriers — raise if it is."""
    carrier = DPD()
    tokens = AuthTokens(access_token="ignored")
    with pytest.raises(CarrierAuthError, match="extension"):
        await carrier.sync_packages(tokens)


async def test_validate_token_is_a_noop():
    """No server-side validation for browser-push — extension handles it."""
    carrier = DPD()
    # Any input is accepted; nothing is raised.
    await carrier.validate_token(AuthTokens(access_token=""))
    await carrier.validate_token(AuthTokens(access_token="<html>", refresh_token="x"))


# --- public (guest) tracking — still server-side, no login required ---


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
    assert result.status == TrackingStatus.IN_TRANSIT


# --- guest-mode detection ---


def test_is_guest_page_positive():
    assert _is_guest_page("<html>Guest User Login</html>")
    assert _is_guest_page("<div>Inloggen/Registreren</div>")
    assert _is_guest_page("Maak een account aan of log in om je pakketten")


def test_is_guest_page_negative():
    assert not _is_guest_page("<html><a href='?parcelNumber=123'>Parcel</a></html>")
    assert not _is_guest_page("")


# --- error page detection ---


def test_is_error_page_positive():
    assert _is_error_page(
        "<html><body><h1>Private customers portal</h1>"
        "<p>Technical issue occurred while processing the request.</p>"
        "</body></html>"
    )
    assert _is_error_page("<p>Er is een technisch probleem opgetreden.</p>")


def test_is_error_page_negative():
    assert not _is_error_page("<html><a href='?parcelNumber=123'>Parcel</a></html>")
    assert not _is_error_page("")


def test_parse_parcels_page_raises_on_error_page():
    carrier = DPD()
    html = (
        "<html><body><h1>Private customers portal</h1>"
        "<p>Technical issue occurred while processing the request.</p>"
        "</body></html>"
    )
    with pytest.raises(CarrierSyncError, match="technical issue"):
        carrier._parse_parcels_page(html)


def test_parse_parcels_page_raises_on_guest_page():
    carrier = DPD()
    html = "<html><body><h2>Guest User Login</h2></body></html>"
    with pytest.raises(CarrierAuthError, match="session expired"):
        carrier._parse_parcels_page(html)
