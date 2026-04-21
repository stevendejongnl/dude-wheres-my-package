import httpx

from dwmp.services.telegram_notifier import TelegramNotifier, _escape_html


def test_escape_html_handles_all_specials():
    raw = "<script>alert('xss & \"evil\"')</script>"
    assert _escape_html(raw) == (
        "&lt;script&gt;alert(&#x27;xss &amp; &quot;evil&quot;&#x27;)&lt;/script&gt;"
    )


def test_disabled_without_credentials():
    notifier = TelegramNotifier(bot_token=None, chat_id=None, pod_name=None)
    assert notifier.enabled is False


def test_enabled_with_both_credentials():
    notifier = TelegramNotifier(bot_token="t", chat_id="c", pod_name=None)
    assert notifier.enabled is True


async def test_send_startup_noop_without_credentials(caplog):
    notifier = TelegramNotifier(bot_token=None, chat_id=None, pod_name=None)
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

    notifier = TelegramNotifier(bot_token="bot-123", chat_id="chat-456", pod_name="dwmp-xyz")
    await notifier.send_startup("9.9.9")

    assert captured["url"] == "https://api.telegram.org/botbot-123/sendMessage"
    assert captured["json"]["chat_id"] == "chat-456"
    assert captured["json"]["parse_mode"] == "HTML"
    assert "DWMP started" in captured["json"]["text"]
    assert "9.9.9" in captured["json"]["text"]
    assert "dwmp-xyz" in captured["json"]["text"]


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

    notifier = TelegramNotifier(bot_token="t", chat_id="c")
    await notifier.send_crash(RuntimeError("boom <bad>"), "1.0.0")

    text = captured["json"]["text"]
    assert "RuntimeError" in text
    assert "boom &lt;bad&gt;" in text  # HTML-escaped
    assert "crashed" in text.lower()


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

    notifier = TelegramNotifier(bot_token="t", chat_id="c")
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
                text = '{"ok":false,"error":"rate limit"}'
            return R()

    monkeypatch.setattr("dwmp.services.telegram_notifier.httpx.AsyncClient", FailingClient)

    notifier = TelegramNotifier(bot_token="t", chat_id="c")
    with caplog.at_level("WARNING"):
        await notifier.send_startup("1.0.0")

    assert any("rejected" in r.message for r in caplog.records)


async def test_env_vars_resolved_when_not_passed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-env")
    monkeypatch.setenv("POD_NAME", "pod-env")
    notifier = TelegramNotifier()
    assert notifier.enabled is True
    assert notifier._pod_name == "pod-env"


async def test_explicit_empty_string_still_disables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    notifier = TelegramNotifier()
    assert notifier.enabled is False
