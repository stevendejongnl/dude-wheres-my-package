import json

import httpx
import pytest

from dwmp.carriers.base import AuthTokens, AuthType, TrackingStatus
from dwmp.carriers.cainiao import CAINIAO_TRACKING_URL, PDN_TRACK_URL, Cainiao, _parse_status


def test_cainiao_is_manual_token():
    assert Cainiao().auth_type == AuthType.MANUAL_TOKEN


def test_parse_status_delivered():
    assert _parse_status("Signed by receiver") == TrackingStatus.DELIVERED
    assert _parse_status("Successfully delivered") == TrackingStatus.DELIVERED


def test_parse_status_out_for_delivery():
    assert _parse_status("Out for delivery") == TrackingStatus.OUT_FOR_DELIVERY
    assert _parse_status("Courier is delivering the parcel") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_in_transit():
    assert _parse_status("Departed from departure country/region") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Import customs clearance started In transit") == TrackingStatus.IN_TRANSIT
    assert _parse_status("[Mayong Town] Processing at sorting center") == TrackingStatus.IN_TRANSIT


def test_parse_status_pre_transit():
    assert _parse_status("Data received") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("Waiting for carrier pickup") == TrackingStatus.PRE_TRANSIT


def test_parse_status_failed():
    assert _parse_status("Delivery failed, no one available") == TrackingStatus.FAILED_ATTEMPT


def test_parse_status_returned():
    assert _parse_status("Return to sender") == TrackingStatus.RETURNED


def test_parse_status_unknown():
    assert _parse_status("Some unrecognized Cainiao status text") == TrackingStatus.UNKNOWN


def test_parse_tracking_response_in_transit():
    carrier = Cainiao()
    payload = {
        "module": [
            {
                "processInfo": {
                    "progressPointList": [
                        {"pointName": "Mainland China", "light": True},
                        {"pointName": "Netherlands", "light": True},
                        {"pointName": "Delivered"},
                    ]
                },
                "detailList": [
                    {
                        "time": 1789289340000,
                        "standerdDesc": "Import customs clearance complete",
                        "actionCode": "CC_IM_SUCCESS",
                        "group": {"nodeCode": "AE_GROUP_IM_CLEARING_CUSTOMS", "nodeDesc": "At customs"},
                    },
                    {
                        "time": 1789196316000,
                        "standerdDesc": "Export customs clearance complete",
                        "actionCode": "CC_EX_SUCCESS",
                        "group": {"nodeCode": "AE_GROUP_EX_CLEARING_CUSTOMS", "nodeDesc": "At customs"},
                    },
                ],
            }
        ],
        "success": True,
    }
    result = carrier._parse_tracking_response("PDN0070419160", payload)
    assert result.tracking_number == "PDN0070419160"
    assert result.carrier == "cainiao"
    assert result.status == TrackingStatus.IN_TRANSIT
    assert len(result.events) == 2
    # Sorted oldest-first
    assert result.events[0].description == "Export customs clearance complete"
    assert result.events[1].description == "Import customs clearance complete"
    assert all(e.status == TrackingStatus.IN_TRANSIT for e in result.events)


def test_parse_tracking_response_delivered_via_progress_points():
    """The final 'Delivered' progress point being lit overrides event text."""
    carrier = Cainiao()
    payload = {
        "module": [
            {
                "processInfo": {
                    "progressPointList": [
                        {"pointName": "Mainland China", "light": True},
                        {"pointName": "Netherlands", "light": True},
                        {"pointName": "Delivered", "light": True},
                    ]
                },
                "detailList": [
                    {
                        "time": 1789289340000,
                        # Ambiguous wording that wouldn't keyword-match DELIVERED on its own.
                        "standerdDesc": "Parcel handed over",
                        "group": {"nodeCode": "AE_GROUP_SIGNED", "nodeDesc": "Delivered"},
                    },
                ],
            }
        ],
        "success": True,
    }
    result = carrier._parse_tracking_response("PDN0070419160", payload)
    assert result.status == TrackingStatus.DELIVERED


def test_parse_tracking_response_no_module():
    carrier = Cainiao()
    result = carrier._parse_tracking_response("NOPE", {"module": [], "success": True})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_empty_detail_list():
    """Unknown/not-yet-scanned tracking numbers come back with an empty detailList."""
    carrier = Cainiao()
    payload = {"module": [{"mailNo": "ZZZZ00000000000", "detailList": []}], "success": True}
    result = carrier._parse_tracking_response("ZZZZ00000000000", payload)
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


async def test_sync_not_supported():
    carrier = Cainiao()
    with pytest.raises(NotImplementedError, match="Cainiao account sync is not supported"):
        await carrier.sync_packages(AuthTokens(access_token="unused"))


_DELIVERED_PAYLOAD = {
    "success": True,
    "module": [
        {
            "processInfo": {
                "progressPointList": [{"pointName": "Delivered", "light": True}],
            },
            "detailList": [
                {
                    "time": 1789531099000,
                    "standerdDesc": "[Netherlands,Amsterdam] Package delivered",
                    "group": {"nodeDesc": "Delivered"},
                },
            ],
        }
    ],
}


def _mock_transport(pod_response: dict | None, pod_status: int = 200):
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(CAINIAO_TRACKING_URL):
            return httpx.Response(200, json=_DELIVERED_PAYLOAD)
        if str(request.url) == PDN_TRACK_URL:
            body = json.loads(request.content)
            assert body["action"] == "pod"
            assert body["orderNo"] == "PDN0070419160"
            assert body["postcode"] == "1431RZ"
            return httpx.Response(pod_status, json=pod_response or {})
        raise AssertionError(f"unexpected request to {request.url}")

    return httpx.MockTransport(handler)


async def test_track_attaches_pdn_proof_photos():
    photos = [
        "https://img.pdn.express/prod/NL/POD/location/2026/09/a.png",
        "https://img.pdn.express/prod/NL/POD/houseno/2026/09/b.png",
    ]
    client = httpx.AsyncClient(transport=_mock_transport({"ok": True, "data": photos}))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("PDN0070419160", postal_code="1431RZ")

    assert result.status == TrackingStatus.DELIVERED
    assert result.events[-1].proof_photos == photos


async def test_track_skips_pdn_lookup_without_postal_code():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(CAINIAO_TRACKING_URL)
        return httpx.Response(200, json=_DELIVERED_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("PDN0070419160")

    assert result.status == TrackingStatus.DELIVERED
    assert result.events[-1].proof_photos is None


async def test_track_skips_pdn_lookup_for_non_pdn_tracking_number():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(CAINIAO_TRACKING_URL)
        return httpx.Response(200, json=_DELIVERED_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("YT1234567890CN", postal_code="1431RZ")

    assert result.status == TrackingStatus.DELIVERED
    assert result.events[-1].proof_photos is None


async def test_track_survives_pdn_lookup_failure():
    """A broken/unreachable PDN API must not break normal Cainiao tracking."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(CAINIAO_TRACKING_URL):
            return httpx.Response(200, json=_DELIVERED_PAYLOAD)
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("PDN0070419160", postal_code="1431RZ")

    assert result.status == TrackingStatus.DELIVERED
    assert result.events[-1].proof_photos is None


async def test_track_ignores_pdn_response_without_photos():
    client = httpx.AsyncClient(transport=_mock_transport({"ok": False, "error": "postcode"}))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("PDN0070419160", postal_code="1431RZ")

    assert result.status == TrackingStatus.DELIVERED
    assert result.events[-1].proof_photos is None


async def test_track_follows_copy_real_mail_no_pivot():
    """AliExpress placeholder order numbers (empty detailList, a
    copyRealMailNo pointing at the actual carrier tracking number) must be
    followed transparently — otherwise polling the placeholder forever
    never surfaces any events."""
    placeholder_payload = {
        "success": True,
        "module": [
            {
                "mailNo": "AP00841686101438",
                "status": "SELLER_PREPARING",
                "detailList": [],
                "copyRealMailNo": "AP00844688431450",
            }
        ],
    }
    real_payload = {
        "success": True,
        "module": [
            {
                "mailNo": "AP00844688431450",
                "processInfo": {"progressPointList": [{"pointName": "Delivered"}]},
                "detailList": [
                    {
                        "time": 1789289340000,
                        "standerdDesc": "Departed from facility",
                        "group": {"nodeDesc": "In transit"},
                    },
                ],
            }
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        mail_no = request.url.params["mailNos"]
        if mail_no == "AP00841686101438":
            return httpx.Response(200, json=placeholder_payload)
        if mail_no == "AP00844688431450":
            return httpx.Response(200, json=real_payload)
        raise AssertionError(f"unexpected mailNos {mail_no}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("AP00841686101438")

    # Reported under the tracking number the user originally added, but
    # carrying the pivot number's real events.
    assert result.tracking_number == "AP00841686101438"
    assert result.status == TrackingStatus.IN_TRANSIT
    assert len(result.events) == 1
    assert result.events[0].description == "Departed from facility"


async def test_track_does_not_pivot_when_own_detail_list_present():
    payload = {
        "success": True,
        "module": [
            {
                "mailNo": "AP1",
                "processInfo": {"progressPointList": []},
                "detailList": [
                    {"time": 1789289340000, "standerdDesc": "In transit"},
                ],
                "copyRealMailNo": "AP2",
            }
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mailNos"] == "AP1"
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("AP1")

    assert result.tracking_number == "AP1"
    assert len(result.events) == 1


async def test_track_pivot_lookup_failure_falls_back_to_placeholder_result():
    """If the pivot lookup itself fails, still return the (empty)
    placeholder result rather than blowing up the whole refresh."""
    placeholder_payload = {
        "success": True,
        "module": [
            {
                "mailNo": "AP1",
                "detailList": [],
                "copyRealMailNo": "AP2",
            }
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        mail_no = request.url.params["mailNos"]
        if mail_no == "AP1":
            return httpx.Response(200, json=placeholder_payload)
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    carrier = Cainiao(http_client=client)

    result = await carrier.track("AP1")

    assert result.tracking_number == "AP1"
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []
