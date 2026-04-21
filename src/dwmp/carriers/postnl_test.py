import httpx
import pytest

from dwmp.carriers.base import AuthTokens, AuthType, TrackingStatus
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
        "colli": {
            "TEST123": {
                "identification": "TEST123-NL-1234AB",
                "statusPhase": {"message": "Bezorgd"},
                "lastObservation": "2026-04-11T14:00:00+02:00",
                "expectedDeliveryDate": "2026-04-12T00:00:00",
                "deliveryAddress": {"address": {"postalCode": "1234AB"}},
                "observations": [
                    {
                        "observationDate": "2026-04-11T10:00:00",
                        "description": "In ontvangst genomen",
                    },
                    {
                        "observationDate": "2026-04-11T14:00:00",
                        "description": "Bezorgd",
                    },
                ],
            }
        }
    }

    result = carrier._parse_json("TEST123", mock_data)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 3
    assert result.events[0].status == TrackingStatus.IN_TRANSIT
    assert result.events[-1].status == TrackingStatus.DELIVERED
    assert result.estimated_delivery is not None
    assert result.postal_code == "1234AB"
    assert result.tracking_url == "https://jouw.postnl.nl/track-and-trace/TEST123-NL-1234AB"


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
        "detailsUrl": "https://jouw.postnl.nl/track-and-trace/3STEST000002-NL-1234AB",
    }
    result = carrier._parse_graphql_shipment(shipment)
    assert result.tracking_number == "3STEST000002"
    assert result.status == TrackingStatus.IN_TRANSIT
    assert result.estimated_delivery is not None
    assert result.postal_code == "1234AB"
    assert result.tracking_url == "https://jouw.postnl.nl/track-and-trace/3STEST000002-NL-1234AB"


async def test_track_uses_public_api_with_tracking_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/track-and-trace/api/trackAndTrace/3STEST000003-NL-1234AB"
        assert request.url.params["language"] == "nl"
        return httpx.Response(
            200,
            json={
                "colli": {
                    "3STEST000003": {
                        "identification": "3STEST000003-NL-1234AB",
                        "statusPhase": {"message": "Bezorger is onderweg"},
                        "lastObservation": "2026-04-21T09:58:33+02:00",
                        "deliveryAddress": {"address": {"postalCode": "1234AB"}},
                        "eta": {"start": "2026-04-21T13:45:00+02:00"},
                        "observations": [
                            {
                                "observationDate": "2026-04-21T09:11:07+02:00",
                                "description": "Zending is gesorteerd",
                            }
                        ],
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PostNL(http_client=client).track(
            "3STEST000003",
            tracking_url="https://jouw.postnl.nl/track-and-trace/3STEST000003-NL-1234AB",
        )

    assert result.status == TrackingStatus.OUT_FOR_DELIVERY
    assert len(result.events) == 2
    assert result.events[-1].description == "Bezorger is onderweg"


async def test_sync_packages_enriches_active_shipments_with_public_timeline():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url == httpx.URL("https://jouw.postnl.nl/account/api/graphql")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "trackedShipments": {
                            "receiverShipments": [
                                {
                                    "barcode": "3STEST000004",
                                    "title": "Pakket van bol",
                                    "delivered": False,
                                    "deliveryWindowFrom": "2026-04-21T13:45:00+02:00",
                                    "creationDateTime": "2026-04-20T17:47:57+02:00",
                                    "detailsUrl": (
                                        "https://jouw.postnl.nl/track-and-trace/"
                                        "3STEST000004-NL-1234AB"
                                    ),
                                }
                            ],
                            "senderShipments": [],
                        }
                    }
                },
            )

        assert request.url.path == "/track-and-trace/api/trackAndTrace/3STEST000004-NL-1234AB"
        return httpx.Response(
            200,
            json={
                "colli": {
                    "3STEST000004": {
                        "identification": "3STEST000004-NL-1234AB",
                        "statusPhase": {"message": "Bezorger is onderweg"},
                        "lastObservation": "2026-04-21T09:58:33+02:00",
                        "deliveryAddress": {"address": {"postalCode": "1234AB"}},
                        "eta": {"start": "2026-04-21T13:45:00+02:00"},
                        "observations": [
                            {
                                "observationDate": "2026-04-21T09:11:07+02:00",
                                "description": "Zending is gesorteerd",
                            },
                            {
                                "observationDate": "2026-04-20T17:47:57+02:00",
                                "description": "Pakket is ontvangen door PostNL",
                            },
                        ],
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await PostNL(http_client=client).sync_packages(
            AuthTokens(access_token="token"),
        )

    assert len(results) == 1
    assert results[0].status == TrackingStatus.OUT_FOR_DELIVERY
    assert len(results[0].events) == 3
    assert results[0].tracking_url == "https://jouw.postnl.nl/track-and-trace/3STEST000004-NL-1234AB"


def test_parse_browser_payload_prefers_detail_payload_for_active_shipments():
    carrier = PostNL()
    results = carrier._parse_browser_payload(
        {
            "shipments": [
                {
                    "barcode": "3STEST000005",
                    "title": "Pakket van bol",
                    "delivered": False,
                    "deliveryWindowFrom": "2026-04-21T13:45:00+02:00",
                    "creationDateTime": "2026-04-20T17:47:57+02:00",
                    "detailsUrl": "https://jouw.postnl.nl/track-and-trace/3STEST000005-NL-1234AB",
                },
                {
                    "barcode": "3STEST000006",
                    "title": "Pakket van bol",
                    "delivered": True,
                    "deliveredTimeStamp": "2026-04-21T14:30:00+02:00",
                    "creationDateTime": "2026-04-20T17:47:57+02:00",
                    "detailsUrl": "https://jouw.postnl.nl/track-and-trace/3STEST000006-NL-1234AB",
                },
            ],
            "details": [
                {
                    "tracking_number": "3STEST000005",
                    "data": {
                        "colli": {
                            "3STEST000005": {
                                "identification": "3STEST000005-NL-1234AB",
                                "statusPhase": {"message": "Bezorger is onderweg"},
                                "lastObservation": "2026-04-21T09:58:33+02:00",
                                "deliveryAddress": {"address": {"postalCode": "1234AB"}},
                                "eta": {"start": "2026-04-21T13:45:00+02:00"},
                                "observations": [
                                    {
                                        "observationDate": "2026-04-21T09:11:07+02:00",
                                        "description": "Zending is gesorteerd",
                                    }
                                ],
                            }
                        }
                    },
                }
            ],
        }
    )

    assert len(results) == 2
    active = next(r for r in results if r.tracking_number == "3STEST000005")
    delivered = next(r for r in results if r.tracking_number == "3STEST000006")
    assert active.status == TrackingStatus.OUT_FOR_DELIVERY
    assert len(active.events) == 2
    assert delivered.status == TrackingStatus.DELIVERED
