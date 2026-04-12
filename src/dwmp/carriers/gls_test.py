import pytest

from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.gls import GLS, _parse_status


def test_gls_is_manual_token():
    assert GLS().auth_type == AuthType.MANUAL_TOKEN


def test_parse_status_delivered():
    assert _parse_status("The parcel has been delivered.") == TrackingStatus.DELIVERED
    assert _parse_status("Bezorgd") == TrackingStatus.DELIVERED
    assert _parse_status("Afgeleverd bij ontvanger") == TrackingStatus.DELIVERED


def test_parse_status_out_for_delivery():
    assert _parse_status("The parcel is out for delivery.") == TrackingStatus.OUT_FOR_DELIVERY
    assert _parse_status("In delivery") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_in_transit():
    assert _parse_status("The parcel has left the parcel center.") == TrackingStatus.IN_TRANSIT
    assert _parse_status("The parcel has reached the parcel center.") == TrackingStatus.IN_TRANSIT
    assert _parse_status("In transit to next facility") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Sorteercentrum bereikt") == TrackingStatus.IN_TRANSIT


def test_parse_status_pre_transit():
    assert _parse_status("The parcel was handed over to GLS.") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("The parcel data was entered in the GLS IT system") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("Preadvice") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("Aangemeld bij GLS") == TrackingStatus.PRE_TRANSIT


def test_parse_status_failed():
    assert _parse_status("The parcel could not be delivered.") == TrackingStatus.FAILED_ATTEMPT
    assert _parse_status("Niet bezorgd") == TrackingStatus.FAILED_ATTEMPT


def test_parse_status_returned():
    assert _parse_status("The parcel has been returned to the sender.") == TrackingStatus.RETURNED
    assert _parse_status("Retour naar afzender") == TrackingStatus.RETURNED


def test_parse_status_unknown():
    assert _parse_status("Some unknown GLS status text") == TrackingStatus.UNKNOWN


def test_parse_tracking_response_delivered():
    carrier = GLS()
    data = {
        "tuStatus": [{
            "progressBar": {"level": 4, "statusInfo": "DELIVERED"},
            "history": [
                {
                    "date": "2026-04-10",
                    "time": "09:00:00",
                    "evtDscr": "The parcel data was entered in the GLS IT system.",
                    "address": {"city": "Eindhoven", "countryName": "Netherlands"},
                },
                {
                    "date": "2026-04-11",
                    "time": "06:30:00",
                    "evtDscr": "The parcel has reached the parcel center.",
                    "address": {"city": "Amsterdam", "countryName": "Netherlands"},
                },
                {
                    "date": "2026-04-11",
                    "time": "14:30:00",
                    "evtDscr": "The parcel has been delivered.",
                    "address": {"city": "Utrecht", "countryName": "Netherlands"},
                },
            ],
        }],
    }
    result = carrier._parse_tracking_response("GLS1234567890", data)
    assert result.tracking_number == "GLS1234567890"
    assert result.carrier == "gls"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 3
    # Events sorted by timestamp
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[0].location == "Eindhoven, Netherlands"
    assert result.events[1].status == TrackingStatus.IN_TRANSIT
    assert result.events[2].status == TrackingStatus.DELIVERED
    assert result.events[2].location == "Utrecht, Netherlands"


def test_parse_tracking_response_in_transit():
    carrier = GLS()
    data = {
        "tuStatus": [{
            "progressBar": {"level": 2, "statusInfo": "INTRANSIT"},
            "history": [
                {
                    "date": "2026-04-11",
                    "time": "08:15",
                    "evtDscr": "The parcel was handed over to GLS.",
                    "address": {"city": "Rotterdam"},
                },
                {
                    "date": "2026-04-11",
                    "time": "12:00",
                    "evtDscr": "The parcel has left the parcel center.",
                    "address": {"city": "Rotterdam", "countryName": "Netherlands"},
                },
            ],
        }],
    }
    result = carrier._parse_tracking_response("GLS9876543210", data)
    assert result.status == TrackingStatus.IN_TRANSIT
    assert len(result.events) == 2
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[0].location == "Rotterdam"
    assert result.events[1].status == TrackingStatus.IN_TRANSIT


def test_parse_tracking_response_empty():
    carrier = GLS()
    result = carrier._parse_tracking_response("NOPE", {})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_empty_history():
    carrier = GLS()
    data = {"tuStatus": [{"progressBar": {}, "history": []}]}
    result = carrier._parse_tracking_response("NOPE", data)
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_status_from_last_event():
    """When progressBar.statusInfo is missing, status comes from last event."""
    carrier = GLS()
    data = {
        "tuStatus": [{
            "progressBar": {},
            "history": [
                {
                    "date": "2026-04-11",
                    "time": "10:00:00",
                    "evtDscr": "The parcel has been delivered.",
                    "address": {},
                },
            ],
        }],
    }
    result = carrier._parse_tracking_response("GLS111", data)
    assert result.status == TrackingStatus.DELIVERED


async def test_gls_rejects_oauth():
    carrier = GLS()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")


async def test_sync_not_supported():
    from dwmp.carriers.base import AuthTokens

    carrier = GLS()
    with pytest.raises(NotImplementedError, match="GLS account sync is not supported"):
        await carrier.sync_packages(AuthTokens(access_token="unused"))
