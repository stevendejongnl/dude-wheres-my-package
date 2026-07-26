import httpx

from dwmp.services.telegram_notifier import TelegramNotifier, _escape_html


def test_escape_html_handles_all_specials():
    raw = "<script>alert('xss & \"evil\"')</script>"
    assert _escape_html(raw) == (
        "&lt;script&gt;alert(&#x27;xss &amp; &quot;evil&quot;&#x27;)&lt;/script&gt;"
    )


def test_disabled_without_apprise_url():
    notifier = TelegramNotifier(apprise_url=None, pod_name=None)
    assert notifier.enabled is False


def test_enabled_with_apprise_url():
    notifier = TelegramNotifier(apprise_url="http://apprise/notify/app-reports", pod_name=None)
    assert notifier.enabled is True


async def test_send_startup_noop_without_apprise_url(caplog):
    notifier = TelegramNotifier(apprise_url=None, pod_name=None)
    with caplog.at_level("INFO"):
        await notifier.send_startup("1.0.0")
    assert any("not configured" in r.message for r in caplog.records)


async def test_send_startup_posts_formatted_message(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("dwmp.services.telegram_notifier.httpx.AsyncClient", FakeClient)

    notifier = TelegramNotifier(apprise_url="http://apprise/notify/app-reports", pod_name="dwmp-xyz")
    await notifier.send_startup("9.9.9")

    assert captured["url"] == "http://apprise/notify/app-reports"
    assert captured["json"]["title"] == "DWMP"
    assert captured["json"]["format"] == "html"
    assert "DWMP started" in captured["json"]["body"]
    assert "9.9.9" in captured["json"]["body"]
    assert "dwmp-xyz" in captured["json"]["body"]


async def test_send_crash_includes_error_details(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("dwmp.services.telegram_notifier.httpx.AsyncClient", FakeClient)

    notifier = TelegramNotifier(apprise_url="http://apprise/notify/app-reports")
    await notifier.send_crash(RuntimeError("boom <bad>"), "1.0.0")

    body = captured["json"]["body"]
    assert "RuntimeError" in body
    assert "boom &lt;bad&gt;" in body  # HTML-escaped
    assert "crashed" in body.lower()


async def test_send_swallows_timeout(caplog, monkeypatch):
    class TimingOutClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            raise httpx.TimeoutException("slow")

    monkeypatch.setattr("dwmp.services.telegram_notifier.httpx.AsyncClient", TimingOutClient)

    notifier = TelegramNotifier(apprise_url="http://apprise/notify/app-reports")
    with caplog.at_level("WARNING"):
        await notifier.send_startup("1.0.0")

    assert any("timed out" in r.message for r in caplog.records)


async def test_send_swallows_non_200_response(caplog, monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            class R:
                status_code = 429
                text = '{"error":"rate limit"}'
            return R()

    monkeypatch.setattr("dwmp.services.telegram_notifier.httpx.AsyncClient", FailingClient)

    notifier = TelegramNotifier(apprise_url="http://apprise/notify/app-reports")
    with caplog.at_level("WARNING"):
        await notifier.send_startup("1.0.0")

    assert any("rejected" in r.message for r in caplog.records)


async def test_env_vars_resolved_when_not_passed(monkeypatch):
    monkeypatch.setenv("APPRISE_URL", "http://apprise/notify/app-reports")
    monkeypatch.setenv("POD_NAME", "pod-env")
    notifier = TelegramNotifier()
    assert notifier.enabled is True
    assert notifier._pod_name == "pod-env"


async def test_explicit_empty_string_still_disables(monkeypatch):
    monkeypatch.setenv("APPRISE_URL", "")
    notifier = TelegramNotifier()
    assert notifier.enabled is False


async def test_send_cloudflare_challenge_posts_carrier(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("dwmp.services.telegram_notifier.httpx.AsyncClient", FakeClient)

    notifier = TelegramNotifier(apprise_url="http://apprise/notify/app-reports", pod_name=None)
    await notifier.send_cloudflare_challenge("dpd")

    assert "Cloudflare challenge" in captured["json"]["body"]
    assert "DPD" in captured["json"]["body"]
