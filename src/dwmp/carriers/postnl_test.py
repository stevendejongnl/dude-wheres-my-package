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


def test_postnl_is_extension_token():
    assert PostNL().auth_type == AuthType.EXTENSION_TOKEN


async def test_postnl_rejects_oauth():
    carrier = PostNL()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")


async def test_postnl_rejects_login():
    carrier = PostNL()
    with pytest.raises(NotImplementedError):
        await carrier.login("user", "pass")


def test_parse_graphql_shipment_delivered():
    carrier = PostNL()
    shipment = {
        "key": "abc123",
        "barcode": "3STEST000001",
        "title": "Pakket van bol",
        "delivered": True,
        "deliveredTimeStamp": "2026-04-10T14:30:00+02:00",
        "deliveryWindowFrom": "2026-04-10T12:00:00+02:00",
        "deliveryWindowTo": "2026-04-10T16:00:00+02:00",
        "creationDateTime": "2026-04-09T08:00:00+02:00",
        "shipmentType": "PARCEL",
    }
    result = carrier._parse_graphql_shipment(shipment)
    assert result.tracking_number == "3STEST000001"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[1].status == TrackingStatus.DELIVERED


def test_parse_graphql_shipment_in_transit():
    carrier = PostNL()
    shipment = {
        "key": "def456",
        "barcode": "3STEST000002",
        "title": "Pakket van Amazon",
        "delivered": False,
        "deliveryWindowFrom": "2026-04-12T09:00:00+02:00",
        "creationDateTime": "2026-04-11T10:00:00+02:00",
    }
    result = carrier._parse_graphql_shipment(shipment)
    assert result.tracking_number == "3STEST000002"
    assert result.status == TrackingStatus.IN_TRANSIT
    assert result.estimated_delivery is not None
