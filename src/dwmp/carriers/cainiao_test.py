import pytest

from dwmp.carriers.base import AuthTokens, AuthType, TrackingStatus
from dwmp.carriers.cainiao import Cainiao, _parse_status


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
