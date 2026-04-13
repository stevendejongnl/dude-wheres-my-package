import logging
from datetime import UTC, datetime

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
        # Stamp the cycle start so we can tell which packages the account
        # sync already touched this pass. Any package with last_refreshed_at
        # >= cycle_start was just written by step 1; step 2 skips it.
        cycle_start = datetime.now(UTC)

        # 1. Sync packages from all connected accounts. This writes status and
        #    stamps last_refreshed_at for every parcel the account still sees.
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

        # 2. Refresh every package that the account sync didn't already touch.
        #    This covers: (a) manual packages, (b) account-discovered packages
        #    that fell off the carrier's account list (delivered, archived,
        #    beyond the lookback window). Before the unified refresh, those
        #    (b) packages stopped updating the moment the account forgot about
        #    them — now they fall through to public track() and keep updating.
        packages = await self._service.list_packages()
        stale = [
            p for p in packages
            if not _refreshed_since(p.get("last_refreshed_at"), cycle_start)
        ]
        logger.info(
            "Synced %d accounts, refreshing %d of %d packages via public track()",
            len(accounts),
            len(stale),
            len(packages),
        )
        for pkg in stale:
            try:
                await self._service.refresh_package(pkg["id"])
            except Exception:
                logger.exception("Failed to refresh package %s", pkg["id"])

        # 3. Clean up old notifications
        deleted = await self._service.delete_old_notifications(days=30)
        if deleted:
            logger.info("Cleaned up %d old notifications", deleted)


def _refreshed_since(last_refreshed_at: str | None, cycle_start: datetime) -> bool:
    """True if the package was refreshed at or after cycle_start.

    None/empty means 'never refreshed' and must be picked up this cycle.
    Malformed ISO strings are treated as 'never refreshed' — better to
    double-refresh than to silently skip.
    """
    if not last_refreshed_at:
        return False
    try:
        ts = datetime.fromisoformat(last_refreshed_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts >= cycle_start
