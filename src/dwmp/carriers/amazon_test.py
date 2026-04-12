import pytest
from datetime import datetime, timezone
from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.amazon import (
    Amazon,
    _parse_cookies,
    _parse_dutch_date,
    _parse_status,
)


def test_amazon_is_manual_token():
    assert Amazon().auth_type == AuthType.MANUAL_TOKEN


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
    assert any(e.status == TrackingStatus.PRE_TRANSIT for e in results[0].events)
    assert any(e.status == TrackingStatus.DELIVERED for e in results[0].events)


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


async def test_track_returns_unknown():
    """Amazon has no public tracking — track() always returns UNKNOWN."""
    carrier = Amazon()
    result = await carrier.track("305-1234567-8901234")
    assert result.status == TrackingStatus.UNKNOWN


async def test_amazon_rejects_oauth():
    carrier = Amazon()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")
