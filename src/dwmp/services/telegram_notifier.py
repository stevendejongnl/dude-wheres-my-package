"""Telegram notification service for deploy + pod-lifecycle events.

The service is a best-effort sidecar: any error (missing config, timeout,
non-2xx response) is logged and swallowed so notification failures never
cascade into app-startup or shutdown failures.

Sends through Apprise (``APPRISE_URL`` env var, pointed at the cluster's
Apprise instance) rather than calling Telegram directly — Apprise owns the
fan-out to Telegram and to the telegram-bridge history log. When unset the
service silently no-ops. The optional ``POD_NAME`` env var (set via
Downward API in k8s) is included in crash/shutdown messages so the user
can correlate which pod restarted.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

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
        apprise_url: str | None = None,
        pod_name: str | None = None,
    ) -> None:
        self._apprise_url = apprise_url if apprise_url is not None else os.environ.get("APPRISE_URL")
        self._pod_name = pod_name if pod_name is not None else os.environ.get("POD_NAME")

    @property
    def enabled(self) -> bool:
        return bool(self._apprise_url)

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

    async def send_cloudflare_challenge(self, carrier: str) -> None:
        """Extension hit a Cloudflare challenge; it runs headless in Kasm so
        the in-browser popup is never seen — Telegram is the visible channel."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = (
            "<b>🤖 Cloudflare challenge</b>\n\n"
            f"<b>Carrier:</b> {_escape_html(carrier.upper())}\n"
            f"<b>Timestamp:</b> {timestamp}\n\n"
            "Open the Kasm browser and solve the check to resume syncing."
        )
        await self._send(message)

    async def _send(self, message: str, *, disable_notification: bool = False) -> None:
        # disable_notification: Apprise's generic /notify API has no
        # per-call silent-delivery flag, so this is currently a no-op —
        # kept for call-site compatibility (send_shutdown passes it).
        if not self.enabled:
            logger.info("APPRISE_URL not configured — skipping notification")
            return
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(
                    self._apprise_url,
                    json={"title": "DWMP", "body": message, "format": "html"},
                )
            if response.status_code != 200:
                logger.warning(
                    "Apprise notification rejected: HTTP %s body=%s",
                    response.status_code, response.text[:200],
                )
        except httpx.TimeoutException:
            logger.warning("Apprise notification timed out")
        except httpx.RequestError as exc:
            logger.warning("Apprise notification failed: %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error sending Apprise notification: %s", exc)
