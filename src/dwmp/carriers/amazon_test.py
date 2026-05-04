from datetime import UTC, datetime

import pytest

from dwmp.carriers.amazon import (
    Amazon,
    _parse_dutch_date,
    _parse_status,
)
from dwmp.carriers.base import AuthTokens, AuthType, CarrierAuthError, TrackingStatus


def test_amazon_is_browser_push():
    assert Amazon().auth_type == AuthType.BROWSER_PUSH


# --- status parsing ---


def test_parse_status_delivered():
    assert _parse_status("Bezorgd op 8 apr.") == TrackingStatus.DELIVERED
    assert _parse_status("Afgeleverd") == TrackingStatus.DELIVERED
    assert _parse_status("Delivered") == TrackingStatus.DELIVERED


def test_parse_status_out_for_delivery():
    assert _parse_status("Wordt vandaag bezorgd") == TrackingStatus.OUT_FOR_DELIVERY
    assert _parse_status("Vandaag verwacht") == TrackingStatus.OUT_FOR_DELIVERY
    assert _parse_status("Out for delivery") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_in_transit():
    assert _parse_status("Onderweg") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Verzonden") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Shipped") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Verwacht op 16 april") == TrackingStatus.IN_TRANSIT


def test_parse_status_future_bezorgd_is_in_transit():
    """'Wordt 14 apr. bezorgd' is future tense — still in transit."""
    assert _parse_status("Wordt 14 apr. bezorgd") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Wordt morgen bezorgd") == TrackingStatus.IN_TRANSIT


def test_parse_status_pre_transit():
    assert _parse_status("Besteld op 5 apr.") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("Ordered") == TrackingStatus.PRE_TRANSIT


def test_parse_status_returned():
    assert _parse_status("Teruggestuurd") == TrackingStatus.RETURNED
    assert _parse_status("Retourgezonden") == TrackingStatus.RETURNED


def test_parse_status_exception():
    assert _parse_status("Geannuleerd") == TrackingStatus.EXCEPTION
    assert _parse_status("Cancelled") == TrackingStatus.EXCEPTION


def test_parse_status_failed_attempt():
    assert _parse_status("Niet bezorgd") == TrackingStatus.FAILED_ATTEMPT
    assert _parse_status("Delivery attempted") == TrackingStatus.FAILED_ATTEMPT


def test_parse_status_unknown():
    assert _parse_status("") == TrackingStatus.UNKNOWN
    assert _parse_status("Some random text") == TrackingStatus.UNKNOWN


# --- date parsing ---


def test_parse_dutch_date_short():
    result = _parse_dutch_date("Bezorgd op 8 apr.")
    assert result is not None
    assert result.day == 8
    assert result.month == 4


def test_parse_dutch_date_long():
    result = _parse_dutch_date("Besteld op 5 april 2026")
    assert result is not None
    assert result.year == 2026
    assert result.month == 4
    assert result.day == 5


def test_parse_dutch_date_no_year_assumes_current():
    result = _parse_dutch_date("Bezorgd op 8 apr.")
    assert result is not None
    assert result.year == datetime.now(UTC).year


def test_parse_dutch_date_invalid():
    assert _parse_dutch_date("") is None
    assert _parse_dutch_date("No date here") is None
    assert _parse_dutch_date("Bezorgd op 32 apr.") is None  # Invalid day


# --- stable fallback timestamps ---


def test_no_date_status_uses_stable_fallback():
    """Status text with no parseable date should use start-of-day, not datetime.now()."""
    carrier = Amazon()
    html = """
    <html><body>
    <div class="order-card">
        <span class="value">305-1111111-3333333</span>
        <div class="delivery-box">
            <span class="delivery-box__primary-text">Wordt vandaag bezorgd</span>
        </div>
    </div>
    </body></html>
    """
    results = carrier._parse_orders_page(html)
    assert len(results) == 1
    # The status event should have a midnight timestamp (start-of-day fallback)
    status_events = [e for e in results[0].events if e.description == "Wordt vandaag bezorgd"]
    assert len(status_events) == 1
    ts = status_events[0].timestamp
    assert ts.hour == 0 and ts.minute == 0 and ts.second == 0 and ts.microsecond == 0


# --- order page HTML parsing (extension-pushed) ---


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
    from bs4 import BeautifulSoup

    carrier = Amazon()
    html = """
    <div class="order-card">
        <div class="yohtmlc-order-id">
            Bestelnummer
            403-4691614-9201953
        </div>
        <div class="a-box delivery-box">
            Bezorgd op 1 april
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    card = soup.select_one(".order-card")
    result = carrier._parse_order_card(card)
    assert result is not None
    assert result.tracking_number == "403-4691614-9201953"
    assert result.status == TrackingStatus.DELIVERED


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


def test_parse_parcels_page_aliases_parse_orders_page():
    """Browser-push path calls _parse_parcels_page; it must match the orders-page parser."""
    carrier = Amazon()
    html = """
    <html><body>
    <div class="order-card">
        <span>305-7777777-7777777</span>
        <span class="a-color-success">Bezorgd</span>
    </div>
    </body></html>
    """
    results = carrier._parse_parcels_page(html)
    assert len(results) == 1
    assert results[0].tracking_number == "305-7777777-7777777"


# --- sync / auth contracts ---


async def test_sync_packages_raises_because_browser_push_only():
    """sync_packages is never called for BROWSER_PUSH carriers — raise if it is."""
    carrier = Amazon()
    tokens = AuthTokens(access_token="ignored")
    with pytest.raises(CarrierAuthError, match="extension"):
        await carrier.sync_packages(tokens)


async def test_validate_token_is_a_noop():
    """No server-side validation for browser-push — extension handles it."""
    carrier = Amazon()
    # Any input is accepted; nothing is raised.
    await carrier.validate_token(AuthTokens(access_token=""))
    await carrier.validate_token(AuthTokens(access_token="not-json", refresh_token="x"))


async def test_track_returns_unknown_without_tracking_url():
    """No tracking_url → we can't hit the public share endpoint → UNKNOWN."""
    carrier = Amazon()
    result = await carrier.track("305-1234567-8901234")
    assert result.status == TrackingStatus.UNKNOWN


async def test_amazon_rejects_oauth():
    carrier = Amazon()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")
