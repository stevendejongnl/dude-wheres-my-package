import pytest

from dwmp.carriers.base import AuthTokens, AuthType, TrackingStatus
from dwmp.carriers.trunkrs import STATE_MAP, Trunkrs, _humanise, _parse_ts


def _make_data(state_name: str, set_at: str = "2026-05-04T10:00:00.000Z", audit_logs: list | None = None) -> dict:
    return {
        "props": {
            "pageProps": {
                "shipment": {
                    "currentState": {"stateName": state_name, "setAt": set_at},
                    "auditLogs": audit_logs or [],
                }
            }
        }
    }


def test_auth_type_is_manual_token():
    assert Trunkrs().auth_type == AuthType.MANUAL_TOKEN


def test_state_map_pre_transit():
    assert STATE_MAP["DATA_PROCESSED"] == TrackingStatus.PRE_TRANSIT
    assert STATE_MAP["DATA_RECEIVED"] == TrackingStatus.PRE_TRANSIT
    assert STATE_MAP["CREATED"] == TrackingStatus.PRE_TRANSIT
    assert STATE_MAP["PICKUP_DRIVER_ASSIGNED"] == TrackingStatus.PRE_TRANSIT


def test_state_map_in_transit():
    assert STATE_MAP["PICKUP_PICKED_UP"] == TrackingStatus.IN_TRANSIT
    assert STATE_MAP["LINEHAUL_IN_TRANSIT"] == TrackingStatus.IN_TRANSIT
    assert STATE_MAP["SHIPMENT_SORTED"] == TrackingStatus.IN_TRANSIT
    assert STATE_MAP["SHIPMENT_DELAYED"] == TrackingStatus.IN_TRANSIT


def test_state_map_out_for_delivery():
    assert STATE_MAP["SHIPMENT_ACCEPTED_BY_DRIVER"] == TrackingStatus.OUT_FOR_DELIVERY


def test_state_map_delivered():
    assert STATE_MAP["SHIPMENT_DELIVERED"] == TrackingStatus.DELIVERED
    assert STATE_MAP["SHIPMENT_DELIVERED_TO_NEIGHBOR"] == TrackingStatus.DELIVERED


def test_state_map_failed_attempt():
    assert STATE_MAP["SHIPMENT_NOT_DELIVERED"] == TrackingStatus.FAILED_ATTEMPT
    assert STATE_MAP["RECIPIENT_NOT_AT_HOME"] == TrackingStatus.FAILED_ATTEMPT
    assert STATE_MAP["MAX_FAILED_DELIVERY_ATTEMPT"] == TrackingStatus.FAILED_ATTEMPT


def test_state_map_returned():
    assert STATE_MAP["REFUSED_BY_CUSTOMER"] == TrackingStatus.RETURNED
    assert STATE_MAP["RETURN_SHIPMENT_TO_SENDER"] == TrackingStatus.RETURNED


def test_state_map_unknown_for_unmapped_state():
    assert STATE_MAP.get("EXCEPTION_SHIPMENT_LOST", TrackingStatus.UNKNOWN) == TrackingStatus.UNKNOWN


def test_parse_tracking_response_pre_transit():
    carrier = Trunkrs()
    result = carrier._parse_tracking_response("418988883", _make_data("DATA_PROCESSED"))
    assert result.tracking_number == "418988883"
    assert result.carrier == "trunkrs"
    assert result.status == TrackingStatus.PRE_TRANSIT
    assert len(result.events) == 1
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[0].description == "Data processed"


def test_parse_tracking_response_delivered():
    carrier = Trunkrs()
    data = {
        "props": {
            "pageProps": {
                "shipment": {
                    "currentState": {"stateName": "SHIPMENT_DELIVERED", "setAt": "2026-05-04T14:30:00.000Z"},
                    "auditLogs": [
                        {"stateName": "DATA_PROCESSED", "setAt": "2026-05-04T06:57:04.000Z"},
                        {"stateName": "PICKUP_PICKED_UP", "setAt": "2026-05-04T10:00:00.000Z"},
                        {"stateName": "SHIPMENT_ACCEPTED_BY_DRIVER", "setAt": "2026-05-04T13:00:00.000Z"},
                        {"stateName": "SHIPMENT_DELIVERED", "setAt": "2026-05-04T14:30:00.000Z"},
                    ],
                }
            }
        }
    }
    result = carrier._parse_tracking_response("418988883", data)
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 4
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[1].status == TrackingStatus.IN_TRANSIT
    assert result.events[2].status == TrackingStatus.OUT_FOR_DELIVERY
    assert result.events[3].status == TrackingStatus.DELIVERED


def test_parse_tracking_response_dedupes_currentstate_in_auditlogs():
    carrier = Trunkrs()
    data = {
        "props": {
            "pageProps": {
                "shipment": {
                    "currentState": {"stateName": "SHIPMENT_SORTED", "setAt": "2026-05-04T12:00:00.000Z"},
                    "auditLogs": [
                        {"stateName": "PICKUP_PICKED_UP", "setAt": "2026-05-04T10:00:00.000Z"},
                        {"stateName": "SHIPMENT_SORTED", "setAt": "2026-05-04T12:00:00.000Z"},
                    ],
                }
            }
        }
    }
    result = carrier._parse_tracking_response("TRK1", data)
    assert len(result.events) == 2


def test_parse_tracking_response_empty_shipment():
    carrier = Trunkrs()
    result = carrier._parse_tracking_response("TRK_NOPE", {"props": {"pageProps": {}}})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_no_current_state():
    carrier = Trunkrs()
    data = {
        "props": {
            "pageProps": {
                "shipment": {
                    "currentState": None,
                    "auditLogs": [],
                }
            }
        }
    }
    result = carrier._parse_tracking_response("TRK_NOPE", data)
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


async def test_track_requires_postal_code():
    carrier = Trunkrs()
    result = await carrier.track("418988883")
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


async def test_track_returns_unknown_on_redirect():
    import httpx

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(302, headers={"location": "https://parcel.trunkrs.nl/failed"})

    client = httpx.AsyncClient(transport=MockTransport())
    carrier = Trunkrs(http_client=client)
    result = await carrier.track("000000000", postal_code="1234AB")
    assert result.status == TrackingStatus.UNKNOWN
    assert result.carrier == "trunkrs"


async def test_track_returns_unknown_when_next_data_missing():
    import httpx

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, html="<html><body>No data here</body></html>")

    client = httpx.AsyncClient(transport=MockTransport())
    carrier = Trunkrs(http_client=client)
    result = await carrier.track("000000000", postal_code="1234AB")
    assert result.status == TrackingStatus.UNKNOWN


async def test_track_sets_tracking_url():
    import httpx

    next_data = (
        '{"props":{"pageProps":{"shipment":{'
        '"currentState":{"stateName":"SHIPMENT_SORTED","setAt":"2026-05-04T10:00:00.000Z"},'
        '"auditLogs":[]}}}}'
    )

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            script = f'<script id="__NEXT_DATA__" type="application/json">{next_data}</script>'
            return httpx.Response(200, html=script)

    client = httpx.AsyncClient(transport=MockTransport())
    carrier = Trunkrs(http_client=client)
    result = await carrier.track("418988883", postal_code="1431rz")
    assert result.tracking_url == "https://parcel.trunkrs.nl/418988883/1431RZ"


async def test_sync_packages_raises():
    carrier = Trunkrs()
    with pytest.raises(NotImplementedError, match="Trunkrs account sync is not supported"):
        await carrier.sync_packages(AuthTokens(access_token="unused"))


async def test_trunkrs_rejects_oauth():
    carrier = Trunkrs()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")


def test_humanise():
    assert _humanise("DATA_PROCESSED") == "Data processed"
    assert _humanise("SHIPMENT_DELIVERED") == "Shipment delivered"


def test_parse_ts_utc_z():
    dt = _parse_ts("2026-05-04T10:00:00.000Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026


def test_parse_ts_invalid_returns_fallback():
    from dwmp.carriers.base import no_date_fallback
    dt = _parse_ts("not-a-date")
    fallback = no_date_fallback()
    assert abs((dt - fallback).total_seconds()) < 2
