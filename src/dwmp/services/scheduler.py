import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from dwmp.carriers.base import CarrierAuthError
from dwmp.services.tracking import TrackingService

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 30


class PackageScheduler:
    def __init__(
        self,
        tracking_service: TrackingService,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    ) -> None:
        self._service = tracking_service
        self._interval = interval_minutes
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._poll_all,
            "interval",
            minutes=self._interval,
            id="poll_packages",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("Scheduler started — polling every %d minutes", self._interval)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    async def _poll_all(self) -> None:
        # 1. Sync packages from all connected accounts
        accounts = await self._service.list_accounts()
        for account in accounts:
            if account["status"] == "auth_failed":
                logger.warning(
                    "Skipping account %s (%s) — auth_failed: %s",
                    account["id"],
                    account["carrier"],
                    account.get("status_message", "unknown"),
                )
                continue

            try:
                await self._service.sync_account(account["id"])
                logger.info("Synced account %s (%s)", account["id"], account["carrier"])
            except CarrierAuthError as exc:
                logger.warning(
                    "Auth failed for account %s (%s): %s. "
                    "The carrier's login flow may have changed — "
                    "try re-authenticating or check for API updates.",
                    account["id"],
                    account["carrier"],
                    exc.message,
                )
                await self._service.notify_auth_failure(
                    carrier=account["carrier"],
                    message=exc.message,
                )
            except Exception:
                logger.exception(
                    "Failed to sync account %s (%s)",
                    account["id"],
                    account["carrier"],
                )

        # 2. Refresh manually tracked packages (no account)
        packages = await self._service.list_packages()
        manual_packages = [p for p in packages if p["source"] == "manual"]
        logger.info(
            "Synced %d accounts, refreshing %d manual packages",
            len(accounts),
            len(manual_packages),
        )
        for pkg in manual_packages:
            try:
                await self._service.refresh_package(pkg["id"])
            except Exception:
                logger.exception("Failed to refresh package %s", pkg["id"])

        # 3. Clean up old notifications
        deleted = await self._service.delete_old_notifications(days=30)
        if deleted:
            logger.info("Cleaned up %d old notifications", deleted)
