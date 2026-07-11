from dwmp.carriers.base import TrackingStatus
from dwmp.carriers.ups import UPS, _map_status


def test_ups_is_manual_token():
    assert UPS().auth_type.value == "manual_token"


def test_map_status_codes():
    assert _map_status("D", "Delivered") == TrackingStatus.DELIVERED
    assert _map_status("I", "Departed from facility") == TrackingStatus.IN_TRANSIT
    assert _map_status("P", "Pickup") == TrackingStatus.IN_TRANSIT
    assert _map_status("M", "Shipment information received") == TrackingStatus.PRE_TRANSIT
    assert _map_status("X", "Exception") == TrackingStatus.EXCEPTION
    assert _map_status("RS", "Returned to shipper") == TrackingStatus.RETURNED


def test_map_status_falls_back_to_description():
    assert _map_status("", "Out For Delivery Today") == TrackingStatus.OUT_FOR_DELIVERY
    assert _map_status("ZZ", "Delivered") == TrackingStatus.DELIVERED
    assert _map_status("", "Something else") == TrackingStatus.UNKNOWN


async def test_track_without_credentials_returns_unknown():
    carrier = UPS(client_id="", client_secret="")
    result = await carrier.track("1Z979Y556807514675")
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_track_response_delivered():
    carrier = UPS()
    data = {
        "trackResponse": {
            "shipment": [
                {
                    "inquiryNumber": "1Z979Y556807514675",
                    "package": [
                        {
                            "trackingNumber": "1Z979Y556807514675",
                            "deliveryDate": [{"type": "DEL", "date": "20260711"}],
                            "deliveryTime": {"type": "DEL", "endTime": "143000"},
                            "activity": [
                                {
                                    "location": {"address": {"city": "AMSTELVEEN", "countryCode": "NL"}},
                                    "status": {"type": "D", "description": "DELIVERED", "code": "FS"},
                                    "date": "20260711",
                                    "time": "103045",
                                },
                                {
                                    "location": {"address": {"city": "EINDHOVEN", "countryCode": "NL"}},
                                    "status": {"type": "I", "description": "Departed from Facility", "code": "DP"},
                                    "date": "20260710",
                                    "time": "221500",
                                },
                            ],
                            "currentStatus": {"code": "011", "description": "Delivered"},
                        }
                    ],
                }
            ]
        }
    }
    result = carrier._parse_track_response("1Z979Y556807514675", data)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    # sorted oldest-first
    assert result.events[0].status == TrackingStatus.IN_TRANSIT
    assert result.events[0].location == "EINDHOVEN, NL"
    assert result.events[1].status == TrackingStatus.DELIVERED
    assert result.events[1].timestamp.isoformat() == "2026-07-11T10:30:45+00:00"


def test_parse_track_response_empty():
    carrier = UPS()
    result = carrier._parse_track_response("1Z000", {"trackResponse": {"shipment": []}})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []
