"""Web Push notification service for package status change events.

Sends browser push notifications to all stored subscriptions using the
VAPID Web Push protocol (RFC 8030). Silently no-ops when VAPID credentials
are not configured. Automatically removes subscriptions that return 410 Gone
(browser has unsubscribed).

VAPID keys are read from env vars VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, and
VAPID_SUBJECT. Generate them once with:

    uv run python -c "
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    print('VAPID_PUBLIC_KEY=' + v.public_key_urlsafe)
    print('VAPID_PRIVATE_KEY=' + v.private_key_urlsafe)
    "
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from pywebpush import WebPushException, webpush

from dwmp.storage.repository import PackageRepository

logger = logging.getLogger(__name__)


class WebPushNotifier:
    def __init__(
        self,
        repository: PackageRepository,
        vapid_private_key: str | None = None,
        vapid_public_key: str | None = None,
        vapid_subject: str | None = None,
    ) -> None:
        self._repository = repository
        self._private_key = (
            vapid_private_key
            if vapid_private_key is not None
            else os.environ.get("VAPID_PRIVATE_KEY", "")
        )
        self._public_key = (
            vapid_public_key
            if vapid_public_key is not None
            else os.environ.get("VAPID_PUBLIC_KEY", "")
        )
        self._subject = (
            vapid_subject
            if vapid_subject is not None
            else os.environ.get("VAPID_SUBJECT", "")
        )

    @property
    def enabled(self) -> bool:
        return bool(self._private_key and self._public_key and self._subject)

    @property
    def public_key(self) -> str:
        return self._public_key

    async def add_subscription(
        self, endpoint: str, p256dh: str, auth: str
    ) -> None:
        await self._repository.add_push_subscription(endpoint, p256dh, auth)

    async def remove_subscription(self, endpoint: str) -> None:
        await self._repository.remove_push_subscription(endpoint)

    async def send_all(self, title: str, body: str, url: str) -> None:
        if not self.enabled:
            return
        subscriptions = await self._repository.get_all_push_subscriptions()
        payload = json.dumps({"title": title, "body": body, "url": url})
        expired: list[str] = []
        for sub in subscriptions:
            subscription_info = {
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            }
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=self._private_key,
                    vapid_claims={"sub": self._subject},
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code == 410:
                    expired.append(sub["endpoint"])
                else:
                    logger.warning("Web push failed for %s: %s", sub["endpoint"][:40], exc)
            except Exception as exc:
                logger.warning("Unexpected push error for %s: %s", sub["endpoint"][:40], exc)
        for endpoint in expired:
            await self._repository.remove_push_subscription(endpoint)
