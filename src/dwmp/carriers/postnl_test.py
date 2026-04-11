import pytest

from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.postnl import PostNL, _parse_status


def test_parse_status_delivered():
    assert _parse_status("Bezorgd") == TrackingStatus.DELIVERED


def test_parse_status_in_transit():
    assert _parse_status("Onderweg naar bestemming") == TrackingStatus.IN_TRANSIT


def test_parse_status_out_for_delivery():
    assert _parse_status("Bezorger is onderweg") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_unknown():
    assert _parse_status("Some unknown text") == TrackingStatus.UNKNOWN


def test_parse_status_case_insensitive():
    assert _parse_status("bezorgd") == TrackingStatus.DELIVERED


async def test_parse_json_response():
    carrier = PostNL()
    mock_data = {
        "colli": [
            {
                "statusPhase": {"message": "Bezorgd"},
                "expectedDeliveryDate": "2026-04-12T00:00:00",
                "events": [
                    {
                        "dateTime": "2026-04-11T10:00:00",
                        "description": "In ontvangst genomen",
                        "location": {"name": "Amsterdam"},
                    },
                    {
                        "dateTime": "2026-04-11T14:00:00",
                        "description": "Bezorgd",
                        "location": {"name": "Utrecht"},
                    },
                ],
            }
        ]
    }

    result = carrier._parse_json("TEST123", mock_data)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].location == "Amsterdam"
    assert result.events[1].status == TrackingStatus.DELIVERED
    assert result.estimated_delivery is not None


def test_postnl_is_oauth():
    assert PostNL().auth_type == AuthType.OAUTH


async def test_get_auth_url():
    carrier = PostNL()
    url = await carrier.get_auth_url("http://localhost/callback")
    assert "login.postnl.nl" in url
    assert "redirect_uri=http://localhost/callback" in url
    assert "response_type=code" in url
