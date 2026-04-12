import pytest

from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.dpd import DPD, _parse_status


def test_dpd_is_manual_token():
    assert DPD().auth_type == AuthType.MANUAL_TOKEN


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
