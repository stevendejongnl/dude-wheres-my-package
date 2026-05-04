from dwmp.carriers.tracking_urls import public_tracking_url


def test_dpd_returns_tracking_url():
    url = public_tracking_url("dpd", "01234567890123456789")
    assert url == "https://www.dpd.com/nl/nl/ontvangen/track-en-trace/?shipmentNumber=01234567890123456789"


def test_dhl_with_postal_code():
    url = public_tracking_url("dhl", "JD000123456", "1234AB")
    assert url == "https://my.dhlecommerce.nl/receiver/track-and-trace/JD000123456/1234AB"


def test_dhl_without_postal_code_returns_root():
    url = public_tracking_url("dhl", "JD000123456")
    assert url == "https://my.dhlecommerce.nl/"


def test_dhl_without_postal_code_explicit_none():
    url = public_tracking_url("dhl", "JD000123456", None)
    assert url == "https://my.dhlecommerce.nl/"


def test_gls_returns_tracking_url():
    url = public_tracking_url("gls", "123456789")
    assert url == "https://gls-group.com/app/service/open/rstt/NL/nl/123456789"


def test_trunkrs_with_postal_code():
    url = public_tracking_url("trunkrs", "418988883", "1431RZ")
    assert url == "https://parcel.trunkrs.nl/418988883/1431RZ"


def test_trunkrs_postal_code_uppercased():
    url = public_tracking_url("trunkrs", "418988883", "1431rz")
    assert url == "https://parcel.trunkrs.nl/418988883/1431RZ"


def test_trunkrs_without_postal_code_returns_root():
    url = public_tracking_url("trunkrs", "418988883")
    assert url == "https://parcel.trunkrs.nl/"


def test_amazon_returns_none():
    assert public_tracking_url("amazon", "123-456-789") is None


def test_postnl_returns_none():
    assert public_tracking_url("postnl", "3SDEVC123456789") is None


def test_browser_returns_none():
    assert public_tracking_url("browser", "anything") is None


def test_unknown_carrier_returns_none():
    assert public_tracking_url("fedex", "123") is None
