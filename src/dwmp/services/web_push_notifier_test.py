from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dwmp.services.web_push_notifier import WebPushNotifier
from dwmp.storage.repository import PackageRepository


def _make_notifier(tmp_path, monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "fake-private")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "fake-public")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:test@example.com")
    repo = MagicMock(spec=PackageRepository)
    return WebPushNotifier(repository=repo)


def test_enabled_when_vapid_keys_set(tmp_path, monkeypatch):
    notifier = _make_notifier(tmp_path, monkeypatch)
    assert notifier.enabled is True


def test_disabled_when_vapid_keys_missing():
    notifier = WebPushNotifier(
        repository=MagicMock(spec=PackageRepository),
        vapid_private_key="",
        vapid_public_key="",
        vapid_subject="",
    )
    assert notifier.enabled is False


def test_public_key_property(tmp_path, monkeypatch):
    notifier = _make_notifier(tmp_path, monkeypatch)
    assert notifier.public_key == "fake-public"


@pytest.mark.asyncio
async def test_send_all_calls_webpush_per_subscription(monkeypatch):
    repo = MagicMock(spec=PackageRepository)
    repo.get_all_push_subscriptions = AsyncMock(return_value=[
        {"endpoint": "https://push.example.com/1", "p256dh": "key1", "auth": "auth1"},
        {"endpoint": "https://push.example.com/2", "p256dh": "key2", "auth": "auth2"},
    ])
    repo.remove_push_subscription = AsyncMock()

    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="priv",
        vapid_public_key="pub",
        vapid_subject="mailto:test@example.com",
    )

    with patch("dwmp.services.web_push_notifier.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = None
        await notifier.send_all("Title", "Body", "/")

    assert mock_thread.call_count == 2
    repo.remove_push_subscription.assert_not_called()


@pytest.mark.asyncio
async def test_send_all_removes_expired_subscription(monkeypatch):
    from pywebpush import WebPushException

    repo = MagicMock(spec=PackageRepository)
    repo.get_all_push_subscriptions = AsyncMock(return_value=[
        {"endpoint": "https://push.example.com/gone", "p256dh": "key", "auth": "auth"},
    ])
    repo.remove_push_subscription = AsyncMock()

    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="priv",
        vapid_public_key="pub",
        vapid_subject="mailto:test@example.com",
    )

    fake_response = MagicMock()
    fake_response.status_code = 410
    exc = WebPushException("Gone", response=fake_response)

    with patch("dwmp.services.web_push_notifier.asyncio.to_thread", side_effect=exc):
        await notifier.send_all("Title", "Body", "/")

    repo.remove_push_subscription.assert_awaited_once_with("https://push.example.com/gone")


@pytest.mark.asyncio
async def test_send_all_no_op_when_disabled():
    repo = MagicMock(spec=PackageRepository)
    repo.get_all_push_subscriptions = AsyncMock(return_value=[])

    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="",
        vapid_public_key="",
        vapid_subject="",
    )

    with patch("dwmp.services.web_push_notifier.asyncio.to_thread") as mock_thread:
        await notifier.send_all("Title", "Body", "/")

    mock_thread.assert_not_called()
