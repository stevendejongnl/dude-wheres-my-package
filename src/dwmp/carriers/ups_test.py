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


async def test_track_without_credentials_uses_browser(monkeypatch):
    """No API creds (UPS API requires a paying account) → Playwright scrape."""
    carrier = UPS(client_id="", client_secret="")
    called = {}

    async def fake_browser_track(tracking_number):
        called["tn"] = tracking_number
        return "browser-result"

    monkeypatch.setattr(carrier, "_track_via_browser", fake_browser_track)
    result = await carrier.track("1Z979Y556807514675")
    assert result == "browser-result"
    assert called["tn"] == "1Z979Y556807514675"


def test_parse_web_json_delivered():
    """The JSON the ups.com track page's own GetStatus XHR returns."""
    carrier = UPS()
    data = {
        "statusCode": "200",
        "trackDetails": [
            {
                "trackingNumber": "1Z979Y556807514675",
                "packageStatus": "Delivered",
                "packageStatusType": "D",
                "scheduledDeliveryDate": "",
                "shipmentProgressActivities": [
                    {
                        # date/time are locale-formatted (DD/MM, 24h for en_NL)
                        # — gmtDate/gmtTime are the reliable fields.
                        "date": "11/07/2026",
                        "time": "12:30",
                        "gmtDate": "20260711",
                        "gmtTime": "10:30:45",
                        "location": "Amstelveen, NL",
                        "activityScan": "Delivered",
                        "trackingStatusType": "D",
                    },
                    {
                        "date": "11/07/2026",
                        "time": "0:15",
                        "gmtDate": "20260710",
                        "gmtTime": "22:15:00",
                        "location": "Eindhoven, NL",
                        "activityScan": "Departed from Facility",
                        "trackingStatusType": None,
                    },
                ],
            }
        ],
    }
    result = carrier._parse_web_json("1Z979Y556807514675", data)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].status == TrackingStatus.IN_TRANSIT
    assert result.events[0].timestamp.isoformat() == "2026-07-10T22:15:00+00:00"
    assert result.events[1].timestamp.isoformat() == "2026-07-11T10:30:45+00:00"
    assert result.events[1].description == "Delivered"
    assert result.events[1].location == "Amstelveen, NL"


def test_parse_web_json_no_details():
    carrier = UPS()
    result = carrier._parse_web_json("1Z000", {"statusCode": "200", "trackDetails": []})
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
