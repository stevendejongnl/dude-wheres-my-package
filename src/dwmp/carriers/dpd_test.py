import pytest
from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.dpd import DPD, _parse_status


def test_parse_status_delivered():
    assert _parse_status("Afgeleverd") == TrackingStatus.DELIVERED


def test_parse_status_in_transit():
    assert _parse_status("Onderweg") == TrackingStatus.IN_TRANSIT


def test_parse_status_out_for_delivery():
    assert _parse_status("Onderweg naar ontvanger") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_pre_transit():
    assert _parse_status("Zending aangekondigd") == TrackingStatus.PRE_TRANSIT


def test_parse_status_unknown():
    assert _parse_status("???") == TrackingStatus.UNKNOWN


async def test_parse_html_with_events():
    carrier = DPD()
    html = """
    <html>
    <body>
        <div class="status-info">Afgeleverd</div>
        <ul class="statusList">
            <li>
                <span class="date">2026-04-11T09:00:00</span>
                <span class="description">Zending aangekondigd</span>
                <span class="location">Amsterdam</span>
            </li>
            <li>
                <span class="date">2026-04-11T14:00:00</span>
                <span class="description">Afgeleverd</span>
                <span class="location">Utrecht</span>
            </li>
        </ul>
    </body>
    </html>
    """
    result = carrier._parse_html("DPD123", html)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].location == "Amsterdam"
    assert result.events[1].status == TrackingStatus.DELIVERED


async def test_parse_empty_html():
    carrier = DPD()
    result = carrier._parse_html("EMPTY", "<html><body></body></html>")
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_dpd_is_credentials():
    assert DPD().auth_type == AuthType.CREDENTIALS


async def test_dpd_rejects_oauth():
    carrier = DPD()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")
