"""Telegram notification service for deploy + pod-lifecycle events.

The service is a best-effort sidecar: any error (missing creds, timeout,
non-2xx response) is logged and swallowed so notification failures never
cascade into app-startup or shutdown failures.

Credentials come from env vars ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID``; when either is missing the service silently no-ops.
The optional ``POD_NAME`` env var (set via Downward API in k8s) is
included in crash/shutdown messages so the user can correlate which pod
restarted.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_HTTP_TIMEOUT = 10.0


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        pod_name: str | None = None,
    ) -> None:
        self._bot_token = bot_token if bot_token is not None else os.environ.get("TELEGRAM_BOT_TOKEN")
        self._chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID")
        self._pod_name = pod_name if pod_name is not None else os.environ.get("POD_NAME")

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def send_startup(self, version: str) -> None:
        """Pod came online — a new deploy just rolled or a pod restarted."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            "<b>🚀 DWMP started</b>\n\n"
            f"<b>Version:</b> {_escape_html(version)}\n"
            f"<b>Timestamp:</b> {timestamp}"
        )
        if self._pod_name:
            message += f"\n<b>Pod:</b> <code>{_escape_html(self._pod_name)}</code>"
        await self._send(message)

    async def send_shutdown(self, version: str, reason: str = "graceful") -> None:
        """Pod is terminating (SIGTERM). Best-effort — may not flight out on SIGKILL/OOM."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            "<b>🔻 DWMP shutdown</b>\n\n"
            f"<b>Reason:</b> {_escape_html(reason)}\n"
            f"<b>Version:</b> {_escape_html(version)}\n"
            f"<b>Timestamp:</b> {timestamp}"
        )
        if self._pod_name:
            message += f"\n<b>Pod:</b> <code>{_escape_html(self._pod_name)}</code>"
        await self._send(message, disable_notification=True)

    async def send_crash(self, error: BaseException, version: str) -> None:
        """Unhandled exception hit the lifespan context — pod is about to die."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        error_type = type(error).__name__
        error_message = _escape_html(str(error)[:500])
        message = (
            "<b>❌ DWMP crashed</b>\n\n"
            f"<b>Error:</b> {error_type}\n"
            f"<b>Message:</b> <code>{error_message}</code>\n"
            f"<b>Version:</b> {_escape_html(version)}\n"
            f"<b>Timestamp:</b> {timestamp}"
        )
        if self._pod_name:
            message += f"\n<b>Pod:</b> <code>{_escape_html(self._pod_name)}</code>"
        await self._send(message)

    async def _send(self, message: str, *, disable_notification: bool = False) -> None:
        if not self.enabled:
            logger.info("Telegram credentials not configured — skipping notification")
            return
        url = _TELEGRAM_API.format(token=self._bot_token)
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_notification": disable_notification,
                    },
                )
            if response.status_code != 200:
                logger.warning(
                    "Telegram notification rejected: HTTP %s body=%s",
                    response.status_code, response.text[:200],
                )
        except httpx.TimeoutException:
            logger.warning("Telegram notification timed out")
        except httpx.RequestError as exc:
            logger.warning("Telegram notification failed: %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error sending Telegram notification: %s", exc)
