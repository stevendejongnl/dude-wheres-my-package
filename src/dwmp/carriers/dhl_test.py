from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.dhl import DHL, _parse_status


def test_parse_status_delivered():
    assert _parse_status("Delivered") == TrackingStatus.DELIVERED


def test_parse_status_in_transit():
    assert _parse_status("In transit") == TrackingStatus.IN_TRANSIT


def test_parse_status_out_for_delivery():
    assert _parse_status("Out for delivery") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_pre_transit():
    assert _parse_status("Shipment information received") == TrackingStatus.PRE_TRANSIT


def test_parse_status_unknown():
    assert _parse_status("Something unexpected") == TrackingStatus.UNKNOWN


async def test_parse_response_with_shipments():
    carrier = DHL()
    mock_data = {
        "results": [
            {
                "status": {"description": "Delivered"},
                "estimatedDeliveryDate": "2026-04-12T00:00:00",
                "events": [
                    {
                        "timestamp": "2026-04-11T09:00:00",
                        "description": "Shipment information received",
                        "location": {"address": {"addressLocality": "Amsterdam"}},
                    },
                    {
                        "timestamp": "2026-04-11T14:00:00",
                        "description": "Delivered",
                        "location": {"address": {"addressLocality": "Utrecht"}},
                    },
                ],
            }
        ]
    }

    result = carrier._parse_response("DHL123", mock_data)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].location == "Amsterdam"
    assert result.events[1].status == TrackingStatus.DELIVERED
    assert result.estimated_delivery is not None


async def test_parse_empty_response():
    carrier = DHL()
    result = carrier._parse_response("EMPTY", {"results": []})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_dhl_is_oauth():
    assert DHL().auth_type == AuthType.OAUTH


async def test_get_auth_url():
    carrier = DHL()
    url = await carrier.get_auth_url("http://localhost/callback")
    assert "redirect_uri=http://localhost/callback" in url
    assert "response_type=code" in url
