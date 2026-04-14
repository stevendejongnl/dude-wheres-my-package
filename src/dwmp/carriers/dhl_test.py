import pytest

from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.dhl import DHL, _parse_status


def test_dhl_is_credentials():
    assert DHL().auth_type == AuthType.CREDENTIALS


def test_parse_status_delivered():
    assert _parse_status("DELIVERED") == TrackingStatus.DELIVERED


def test_parse_status_in_transit():
    assert _parse_status("IN_TRANSIT") == TrackingStatus.IN_TRANSIT


def test_parse_status_out_for_delivery():
    assert _parse_status("DOOR_DELIVERY") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_pre_transit():
    assert _parse_status("PRENOTIFICATION_RECEIVED") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("DATA_RECEIVED") == TrackingStatus.PRE_TRANSIT


def test_parse_status_unknown():
    assert _parse_status("SOMETHING_ELSE") == TrackingStatus.UNKNOWN


def test_parse_parcel_delivered():
    carrier = DHL()
    parcel = {
        "parcelId": "abc-123",
        "barcode": "3SQLW0036293283",
        "status": "DELIVERED",
        "category": "DELIVERED",
        "sender": {"name": "Brandpreventiewinkel"},
        "createdAt": "2026-03-29T16:13:54.692834Z",
        "receivingTimeIndication": {
            "moment": "2026-03-30T12:35:28Z",
            "indicationType": "MomentIndication",
        },
    }
    result = carrier._parse_parcel(parcel)
    assert result.tracking_number == "3SQLW0036293283"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 2
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[0].description == "Brandpreventiewinkel"
    assert result.events[1].status == TrackingStatus.DELIVERED


def test_parse_parcel_pre_transit():
    carrier = DHL()
    parcel = {
        "parcelId": "def-456",
        "barcode": "CQ964395186DE",
        "status": "PRENOTIFICATION_RECEIVED",
        "category": "DATA_RECEIVED",
        "sender": {"name": "GCDE-6  # 11WG14873"},
        "createdAt": "2026-04-11T05:06:54.237475Z",
        "receivingTimeIndication": None,
    }
    result = carrier._parse_parcel(parcel)
    assert result.tracking_number == "CQ964395186DE"
    assert result.status == TrackingStatus.PRE_TRANSIT
    assert len(result.events) == 1
    assert result.estimated_delivery is None


def test_parse_parcel_no_events():
    carrier = DHL()
    parcel = {
        "barcode": "EMPTY",
        "status": "UNKNOWN",
    }
    result = carrier._parse_parcel(parcel)
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_unified_response():
    """Full DHL Unified API response → rich event timeline."""
    carrier = DHL()
    data = {
        "shipments": [{
            "id": "CQ964395186DE",
            "status": {
                "statusCode": "transit",
                "description": "The shipment has been loaded onto the delivery vehicle",
            },
            "events": [
                {
                    "timestamp": "2026-04-14T10:11:00",
                    "statusCode": "transit",
                    "description": "The shipment has been loaded onto the delivery vehicle",
                    "location": {"address": {"addressLocality": "Netherlands"}},
                },
                {
                    "timestamp": "2026-04-13T16:05:00",
                    "statusCode": "transit",
                    "description": "The shipment has arrived in the destination country (<a href='https://example.com'>link</a>)",
                    "location": {"address": {"addressLocality": "Netherlands"}},
                },
                {
                    "timestamp": "2026-04-10T18:23:00",
                    "statusCode": "pre-transit",
                    "description": "Instruction data provided by sender",
                },
            ],
        }],
    }
    result = carrier._parse_unified_response("CQ964395186DE", data)
    assert result.tracking_number == "CQ964395186DE"
    assert result.status == TrackingStatus.IN_TRANSIT
    assert len(result.events) == 3
    # Events sorted by timestamp
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[2].status == TrackingStatus.IN_TRANSIT
    assert result.events[2].location == "Netherlands"
    # HTML stripped from descriptions
    assert "<a" not in result.events[1].description


def test_parse_unified_response_empty():
    carrier = DHL()
    result = carrier._parse_unified_response("UNKNOWN", {"shipments": []})
    assert result.status == TrackingStatus.UNKNOWN


async def test_track_uses_api_when_key_set(monkeypatch):
    """With DHL_API_KEY set, track() calls the Unified API."""
    monkeypatch.setattr("dwmp.carriers.dhl.DHL_API_KEY", "test-key-123")

    captured: list[dict] = []

    async def fake_get(self, url, **kwargs):
        captured.append({"url": url, "headers": kwargs.get("headers", {})})

        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"shipments": [{
                    "id": "TEST123",
                    "status": {"statusCode": "delivered", "description": "Delivered"},
                    "events": [{
                        "timestamp": "2026-04-14T10:00:00",
                        "statusCode": "delivered",
                        "description": "Delivered to mailbox",
                        "location": {"address": {"addressLocality": "Amsterdam"}},
                    }],
                }]}
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)

    carrier = DHL()
    result = await carrier.track("TEST123")
    assert len(captured) == 1
    assert "api-eu.dhl.com" in captured[0]["url"]
    assert captured[0]["headers"]["DHL-API-Key"] == "test-key-123"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 1


async def test_dhl_rejects_oauth():
    carrier = DHL()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")
