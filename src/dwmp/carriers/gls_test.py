import pytest

from dwmp.carriers.base import AuthType, TrackingStatus
from dwmp.carriers.gls import GLS, _parse_status


def test_gls_is_manual_token():
    assert GLS().auth_type == AuthType.MANUAL_TOKEN


def test_parse_status_delivered():
    assert _parse_status("Afgeleverd") == TrackingStatus.DELIVERED
    assert _parse_status("Bezorgd bij ontvanger") == TrackingStatus.DELIVERED
    assert _parse_status("Delivered") == TrackingStatus.DELIVERED


def test_parse_status_out_for_delivery():
    assert _parse_status("Onderweg - geladen voor aflevering") == TrackingStatus.OUT_FOR_DELIVERY
    assert _parse_status("Out for delivery") == TrackingStatus.OUT_FOR_DELIVERY


def test_parse_status_in_transit():
    assert _parse_status("Doorgestuurd naar GLS depot") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Aangekomen op GLS depot") == TrackingStatus.IN_TRANSIT
    assert _parse_status("Pakket ontvangen door GLS") == TrackingStatus.IN_TRANSIT


def test_parse_status_pre_transit():
    assert _parse_status("Aangekondigd bij GLS") == TrackingStatus.PRE_TRANSIT
    assert _parse_status("Pakket gereed voor overdracht aan GLS") == TrackingStatus.PRE_TRANSIT


def test_parse_status_failed():
    assert _parse_status("Niet afgeleverd") == TrackingStatus.FAILED_ATTEMPT
    assert _parse_status("Niet bezorgd") == TrackingStatus.FAILED_ATTEMPT


def test_parse_status_returned():
    assert _parse_status("Retour naar afzender") == TrackingStatus.RETURNED
    assert _parse_status("Returned to sender") == TrackingStatus.RETURNED


def test_parse_status_unknown():
    assert _parse_status("Some unknown GLS status text") == TrackingStatus.UNKNOWN


def test_parse_tracking_response_delivered():
    carrier = GLS()
    data = {
        "parcelNo": "92070059413077",
        "deliveryScanInfo": {"isDelivered": True},
        "addressInfo": {"from": {"name": "Vlaggen Unie B.V."}},
        "scans": [
            {
                "dateTime": "2026-03-30T09:12:43.716",
                "eventReasonDescr": "Aangekondigd bij GLS",
                "depotName": "-",
                "countryName": "Nederland",
            },
            {
                "dateTime": "2026-03-30T18:31:38.371",
                "eventReasonDescr": "Pakket ontvangen door GLS",
                "depotName": "Drachten",
                "countryName": "Nederland",
            },
            {
                "dateTime": "2026-03-31T08:47:00",
                "eventReasonDescr": "Onderweg - geladen voor aflevering",
                "depotName": "Amsterdam",
                "countryName": "Nederland",
            },
            {
                "dateTime": "2026-03-31T17:58:24",
                "eventReasonDescr": "Afgeleverd",
                "depotName": "Amsterdam",
                "countryName": "Nederland",
            },
        ],
    }
    result = carrier._parse_tracking_response("92070059413077", data)
    assert result.tracking_number == "92070059413077"
    assert result.carrier == "gls"
    assert result.status == TrackingStatus.DELIVERED
    assert len(result.events) == 4
    # Events sorted by timestamp
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[0].location == "Nederland"  # depotName "-" is filtered
    assert result.events[1].status == TrackingStatus.IN_TRANSIT
    assert result.events[1].location == "Drachten, Nederland"
    assert result.events[2].status == TrackingStatus.OUT_FOR_DELIVERY
    assert result.events[3].status == TrackingStatus.DELIVERED


def test_parse_tracking_response_in_transit():
    carrier = GLS()
    data = {
        "deliveryScanInfo": {"isDelivered": False},
        "scans": [
            {
                "dateTime": "2026-04-11T09:00:00",
                "eventReasonDescr": "Aangekondigd bij GLS",
                "depotName": "-",
                "countryName": "Nederland",
            },
            {
                "dateTime": "2026-04-11T18:00:00",
                "eventReasonDescr": "Doorgestuurd naar GLS depot",
                "depotName": "Drachten",
                "countryName": "Nederland",
            },
        ],
    }
    result = carrier._parse_tracking_response("GLS9876543210", data)
    assert result.status == TrackingStatus.IN_TRANSIT
    assert len(result.events) == 2
    assert result.events[0].status == TrackingStatus.PRE_TRANSIT
    assert result.events[1].status == TrackingStatus.IN_TRANSIT


def test_parse_tracking_response_empty():
    carrier = GLS()
    result = carrier._parse_tracking_response("NOPE", {})
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_no_scans():
    carrier = GLS()
    data = {"deliveryScanInfo": {"isDelivered": False}, "scans": []}
    result = carrier._parse_tracking_response("NOPE", data)
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


def test_parse_tracking_response_status_from_delivery_info():
    """When deliveryScanInfo.isDelivered is true, status is DELIVERED regardless of events."""
    carrier = GLS()
    data = {
        "deliveryScanInfo": {"isDelivered": True},
        "scans": [
            {
                "dateTime": "2026-04-11T10:00:00",
                "eventReasonDescr": "Aangekondigd bij GLS",
                "depotName": "-",
                "countryName": "Nederland",
            },
        ],
    }
    result = carrier._parse_tracking_response("GLS111", data)
    assert result.status == TrackingStatus.DELIVERED


async def test_track_requires_postal_code():
    """Track without postal_code returns UNKNOWN immediately."""
    carrier = GLS()
    result = await carrier.track("92070059413077")
    assert result.status == TrackingStatus.UNKNOWN
    assert result.events == []


async def test_gls_rejects_oauth():
    carrier = GLS()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")


async def test_sync_not_supported():
    from dwmp.carriers.base import AuthTokens

    carrier = GLS()
    with pytest.raises(NotImplementedError, match="GLS account sync is not supported"):
        await carrier.sync_packages(AuthTokens(access_token="unused"))
