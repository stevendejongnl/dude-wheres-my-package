import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from dwmp.carriers.base import CarrierAuthError, CarrierTransientError
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
        self._scheduler.add_job(
            self._reauth_probe,
            "interval",
            hours=24,
            id="reauth_probe",
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
            if not account.get("sync_enabled", True):
                logger.info(
                    "Skipping account %s (%s) — sync disabled",
                    account["id"],
                    account["carrier"],
                )
                continue
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
            except CarrierTransientError as exc:
                logger.info(
                    "Transient failure syncing account %s (%s): %s — will retry next cycle",
                    account["id"],
                    account["carrier"],
                    exc.message,
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
            and not _should_skip(p)
        ]
        skipped = sum(1 for p in packages if _should_skip(p))
        logger.info(
            "Synced %d accounts, refreshing %d of %d packages via public track() (%d skipped as stale)",
            len(accounts),
            len(stale),
            len(packages),
            skipped,
        )
        for pkg in stale:
            try:
                await self._service.refresh_package(pkg["id"])
            except Exception:
                logger.exception("Failed to refresh package %s", pkg["id"])
            await asyncio.sleep(0.5)

        # 3. Clean up old notifications
        deleted = await self._service.delete_old_notifications(days=30)
        if deleted:
            logger.info("Cleaned up %d old notifications", deleted)

    async def _reauth_probe(self) -> None:
        """Once per day, probe auth_failed accounts to see if they can recover.

        Attempts a lightweight credential validation for each stuck account.
        On success the account transitions back to CONNECTED without any
        user action, covering accounts that were bricked by transient network
        failures rather than genuine credential problems.
        """
        accounts = await self._service.list_accounts()
        stuck = [a for a in accounts if a.get("status") == "auth_failed"]
        if not stuck:
            return
        logger.info("Re-auth probe: checking %d stuck account(s)", len(stuck))
        for account in stuck:
            try:
                await self._service.validate_account_credentials_by_id(account["id"])
                logger.info(
                    "Account %s (%s) recovered from auth_failed",
                    account["id"], account["carrier"],
                )
            except Exception as exc:
                logger.debug(
                    "Account %s (%s) still auth_failed: %s",
                    account["id"], account["carrier"], exc,
                )


_DELIVERED_CUTOFF_DAYS = 14
_MAX_CONSECUTIVE_FAILURES = 5
_STALE_REPROBE_HOURS = 24


def _should_skip(pkg: dict) -> bool:
    """Return True if a package should be excluded from the refresh loop.

    Two conditions trigger a skip:
    - Delivered and last updated more than 14 days ago — the carrier page is
      stale, nothing new will appear.
    - 5+ consecutive tracking failures — the carrier can no longer resolve this
      package right now. Rather than skip forever (which would freeze the
      package even if the carrier's outage was transient), it gets re-probed
      once every _STALE_REPROBE_HOURS — same as auth_failed accounts.
    """
    if pkg.get("consecutive_failures", 0) >= _MAX_CONSECUTIVE_FAILURES:
        last_refreshed_at = pkg.get("last_refreshed_at")
        if not last_refreshed_at:
            return False
        try:
            ts = datetime.fromisoformat(last_refreshed_at)
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return datetime.now(UTC) - ts < timedelta(hours=_STALE_REPROBE_HOURS)

    if pkg.get("current_status") == "delivered":
        updated = pkg.get("updated_at")
        if updated:
            try:
                ts = datetime.fromisoformat(updated)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if datetime.now(UTC) - ts > timedelta(days=_DELIVERED_CUTOFF_DAYS):
                    return True
            except ValueError:
                pass

    return False


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
