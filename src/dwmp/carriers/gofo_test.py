import base64

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from dwmp.carriers.base import AuthTokens, AuthType, TrackingStatus
from dwmp.carriers.gofo import CODE_MAP, GoFo, _decrypt_pod_url


def _encrypt(url: str, pod_code: str, number: str) -> str:
    """Inverse of gofo.com's decryptTextWithKey(), for building test fixtures."""
    combo = f"{pod_code}{number}"
    key_str = combo[:16] if len(combo) >= 16 else combo.ljust(16, "0")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(url.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key_str.encode("utf-8")), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def _event(process_code: str, content: str, date: str) -> dict:
    return {
        "processDate": date,
        "processContent": content,
        "processLocation": "",
        "processCode": process_code,
        "mainContent": content,
        "subContent": None,
        "trackStatus": "2",
    }


def _record(waybill_no: str, events: list[dict], **extra) -> dict:
    return {
        "waybillNo": waybill_no,
        "trackingNumber": "SYNL010939284_18080617",
        "status": "InTransit",
        "number": "3351041701",
        "lastTrackEvent": events[0] if events else {},
        "trackEventList": events,
        "podImgList": [],
        **extra,
    }


def test_auth_type_is_manual_token():
    assert GoFo().auth_type == AuthType.MANUAL_TOKEN


def test_code_map_pre_transit():
    assert CODE_MAP["100"] == TrackingStatus.PRE_TRANSIT
    assert CODE_MAP["282"] == TrackingStatus.PRE_TRANSIT


def test_code_map_in_transit():
    assert CODE_MAP["200"] == TrackingStatus.IN_TRANSIT
    assert CODE_MAP["202"] == TrackingStatus.IN_TRANSIT
    assert CODE_MAP["203"] == TrackingStatus.IN_TRANSIT


def test_code_map_out_for_delivery():
    assert CODE_MAP["208"] == TrackingStatus.OUT_FOR_DELIVERY
    assert CODE_MAP["281"] == TrackingStatus.OUT_FOR_DELIVERY


def test_code_map_delivered():
    assert CODE_MAP["205"] == TrackingStatus.DELIVERED


def test_code_map_exception():
    assert CODE_MAP["204"] == TrackingStatus.EXCEPTION
    assert CODE_MAP["300"] == TrackingStatus.EXCEPTION


def test_code_map_returned():
    assert CODE_MAP["257"] == TrackingStatus.RETURNED
    assert CODE_MAP["264"] == TrackingStatus.RETURNED


def test_parse_tracking_response_delivered():
    carrier = GoFo()
    data = {
        "data": [
            _record(
                "GFNL26261188942030",
                [
                    _event("205", "Bezorgd op het bezorgadres", "2026-09-30T09:02:20.000+0200"),
                    _event("208", "Bezorger is onderweg", "2026-09-30T07:35:11.000+0200"),
                    _event("100", "Zending nog niet ontvangen", "2026-09-18T00:06:18.000Z"),
                ],
            )
        ]
    }
    result = carrier._parse_tracking_response("GFNL26261188942030", data)
    assert result.tracking_number == "GFNL26261188942030"
    assert result.carrier == "gofo"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 3
    # Sorted ascending by timestamp — oldest first, latest last
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[1].status == TrackingStatus.OUT_FOR_DELIVERY
    assert result.events[2].status == TrackingStatus.DELIVERED


def test_parse_tracking_response_matches_case_insensitively():
    carrier = GoFo()
    data = {"data": [_record("GFNL26261188942030", [_event("100", "x", "2026-09-18T00:06:18.000Z")])]}
    result = carrier._parse_tracking_response("gfnl26261188942030", data)
    assert result.status == TrackingStatus.PRE_TRANSIT


def test_parse_tracking_response_not_found():
    carrier = GoFo()
    result = carrier._parse_tracking_response("GFNL00000000000000", {"data": []})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_unmapped_code_falls_back_to_last_event():
    carrier = GoFo()
    data = {
        "data": [
            _record(
                "GFNL1",
                [_event("999", "Some new status gofo hasn't documented", "2026-09-18T00:06:18.000Z")],
            )
        ]
    }
    result = carrier._parse_tracking_response("GFNL1", data)
    assert result.status == TrackingStatus.UNKNOWN


def test_pod_photos_decrypted_when_delivered_and_code_provided():
    carrier = GoFo()
    photo_url = "https://dbu-cps-nld-admin.s3.eu-central-1.amazonaws.com/photo.jpg?sig=abc"
    encrypted = _encrypt(photo_url, "800157", "3351041701")
    data = {
        "data": [
            _record(
                "GFNL26261188942030",
                [_event("205", "Bezorgd", "2026-09-30T09:02:20.000+0200")],
                podImgList=[encrypted],
            )
        ]
    }
    result = carrier._parse_tracking_response("GFNL26261188942030", data, pod_code="800157")
    assert result.status == TrackingStatus.DELIVERED
    assert result.events[-1].proof_photos == [photo_url]


def test_pod_photos_not_decrypted_without_pod_code():
    carrier = GoFo()
    encrypted = _encrypt("https://example.com/photo.jpg", "800157", "3351041701")
    data = {
        "data": [
            _record(
                "GFNL26261188942030",
                [_event("205", "Bezorgd", "2026-09-30T09:02:20.000+0200")],
                podImgList=[encrypted],
            )
        ]
    }
    result = carrier._parse_tracking_response("GFNL26261188942030", data, pod_code="")
    assert result.events[-1].proof_photos is None


def test_pod_photos_wrong_code_yields_no_photos():
    carrier = GoFo()
    encrypted = _encrypt("https://example.com/photo.jpg", "800157", "3351041701")
    data = {
        "data": [
            _record(
                "GFNL26261188942030",
                [_event("205", "Bezorgd", "2026-09-30T09:02:20.000+0200")],
                podImgList=[encrypted],
            )
        ]
    }
    result = carrier._parse_tracking_response("GFNL26261188942030", data, pod_code="000000")
    assert result.events[-1].proof_photos is None


def test_pod_photos_not_attached_when_not_delivered():
    carrier = GoFo()
    encrypted = _encrypt("https://example.com/photo.jpg", "800157", "3351041701")
    data = {
        "data": [
            _record(
                "GFNL26261188942030",
                [_event("208", "Bezorger is onderweg", "2026-09-30T07:35:11.000+0200")],
                podImgList=[encrypted],
            )
        ]
    }
    result = carrier._parse_tracking_response("GFNL26261188942030", data, pod_code="800157")
    assert result.events[-1].proof_photos is None


def test_decrypt_pod_url_roundtrip():
    encrypted = _encrypt("https://example.com/a.jpg", "800157", "3351041701")
    assert _decrypt_pod_url(encrypted, "800157", "3351041701") == "https://example.com/a.jpg"


def test_decrypt_pod_url_garbage_input_returns_none():
    assert _decrypt_pod_url("not-valid-base64!!", "800157", "3351041701") is None


async def test_track_posts_number_list_and_parses_result():
    captured_request = {}

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            captured_request["body"] = request.content
            captured_request["url"] = str(request.url)
            payload = {
                "msg": "ok",
                "code": 200,
                "data": [_record("GFNL1", [_event("205", "Bezorgd", "2026-09-30T09:02:20.000+0200")])],
            }
            return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=MockTransport())
    carrier = GoFo(http_client=client)
    result = await carrier.track("GFNL1")
    assert result.status == TrackingStatus.DELIVERED
    assert captured_request["url"] == "https://www.gofo.com/nl/open-api/official/track/queryTrackV2"
    assert b'"numberList"' in captured_request["body"]
    assert b"GFNL1" in captured_request["body"]


async def test_track_does_not_require_pod_code():
    """Unlike GLS/Trunkrs/DPD, GoFo's basic tracking works without the pod
    code — it's only needed to decrypt delivery-proof photos."""

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            payload = {"code": 200, "data": [_record("GFNL1", [_event("100", "x", "2026-09-18T00:06:18.000Z")])]}
            return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=MockTransport())
    carrier = GoFo(http_client=client)
    result = await carrier.track("GFNL1")
    assert result.status == TrackingStatus.PRE_TRANSIT


async def test_sync_packages_raises():
    carrier = GoFo()
    with pytest.raises(NotImplementedError, match="GoFo account sync is not supported"):
        await carrier.sync_packages(AuthTokens(access_token="unused"))
