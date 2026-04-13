from dwmp.carriers.browser import _normalize_cookies, _platform_from_ua


def test_normalize_playwright_format():
    raw = [{"name": "sid", "value": "123", "domain": ".example.com",
            "path": "/", "expires": 1700000000, "httpOnly": True,
            "secure": True, "sameSite": "Lax"}]
    result = _normalize_cookies(raw)
    assert result[0]["name"] == "sid"
    assert result[0]["expires"] == 1700000000
    assert result[0]["sameSite"] == "Lax"


def test_normalize_cookie_editor_format():
    """Cookie-Editor uses expirationDate and lowercase sameSite."""
    raw = [{"name": "tok", "value": "abc", "domain": ".amazon.nl",
            "path": "/", "expirationDate": 1700000000,
            "httpOnly": False, "secure": True, "sameSite": "lax"}]
    result = _normalize_cookies(raw)
    assert result[0]["expires"] == 1700000000
    assert result[0]["sameSite"] == "Lax"


def test_normalize_minimal_cookie():
    raw = [{"name": "x", "value": "y"}]
    result = _normalize_cookies(raw)
    assert result[0]["name"] == "x"
    assert result[0]["domain"] == ""
    assert result[0]["path"] == "/"


def test_normalize_bad_samesite():
    raw = [{"name": "x", "value": "y", "sameSite": "unspecified"}]
    result = _normalize_cookies(raw)
    assert result[0]["sameSite"] == "Lax"


def test_platform_from_ua_windows():
    assert _platform_from_ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64)") == "Win32"


def test_platform_from_ua_mac():
    assert _platform_from_ua(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)"
    ) == "MacIntel"


def test_platform_from_ua_linux_default():
    assert _platform_from_ua("Mozilla/5.0 (X11; Linux x86_64)") == "Linux x86_64"
    assert _platform_from_ua("something/weird") == "Linux x86_64"
