import pytest
from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.dhl import DHL, _parse_status


def test_dhl_is_credentials():
    assert DHL().auth_type == AuthType.CREDENTIALS


def test_parse_status_delivered():
    assert _parse_status("DELIVERED") == TrackingStatus.DELIVERED


def test_parse_status_in_transit():
    assert _parse_status("IN_TRANSIT") == TrackingStatus.IN_TRANSIT


def test_parse_status_out_for_delivery():
    assert _parse_status("DOOR_DELIVERY") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_pre_transit():
    assert _parse_status("PRENOTIFICATION_RECEIVED") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("DATA_RECEIVED") == TrackingStatus.PRE_TRANSIT


def test_parse_status_unknown():
    assert _parse_status("SOMETHING_ELSE") == TrackingStatus.UNKNOWN


def test_parse_parcel_delivered():
    carrier = DHL()
    parcel = {
        "parcelId": "abc-123",
        "barcode": "3SQLW0036293283",
        "status": "DELIVERED",
        "category": "DELIVERED",
        "sender": {"name": "Brandpreventiewinkel"},
        "createdAt": "2026-03-29T16:13:54.692834Z",
        "receivingTimeIndication": {
            "moment": "2026-03-30T12:35:28Z",
            "indicationType": "MomentIndication",
        },
    }
    result = carrier._parse_parcel(parcel)
    assert result.tracking_number == "3SQLW0036293283"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[0].description == "Brandpreventiewinkel"
    assert result.events[1].status == TrackingStatus.DELIVERED


def test_parse_parcel_pre_transit():
    carrier = DHL()
    parcel = {
        "parcelId": "def-456",
        "barcode": "CQ964395186DE",
        "status": "PRENOTIFICATION_RECEIVED",
        "category": "DATA_RECEIVED",
        "sender": {"name": "GCDE-6  # 11WG14873"},
        "createdAt": "2026-04-11T05:06:54.237475Z",
        "receivingTimeIndication": None,
    }
    result = carrier._parse_parcel(parcel)
    assert result.tracking_number == "CQ964395186DE"
    assert result.status == TrackingStatus.PRE_TRANSIT
    assert len(result.events) == 1
    assert result.estimated_delivery is None


def test_parse_parcel_no_events():
    carrier = DHL()
    parcel = {
        "barcode": "EMPTY",
        "status": "UNKNOWN",
    }
    result = carrier._parse_parcel(parcel)
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


async def test_dhl_rejects_oauth():
    carrier = DHL()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")
