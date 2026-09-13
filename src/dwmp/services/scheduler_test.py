from datetime import UTC, datetime, timedelta

import pytest

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
    CarrierTransientError,
    TrackingResult,
    TrackingStatus,
)
from dwmp.services.scheduler import PackageScheduler
from dwmp.services.tracking import TrackingService
from dwmp.storage.repository import PackageRepository


class StubCarrier(CarrierBase):
    name = "stub"
    auth_type = AuthType.CREDENTIALS
    track_count = 0
    sync_count = 0

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        self.track_count += 1
        return TrackingResult(
            tracking_number=tracking_number,
            carrier="stub",
            status=TrackingStatus.IN_TRANSIT,
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        self.sync_count += 1
        return [
            TrackingResult(
                tracking_number="FROM-ACCOUNT",
                carrier="stub",
                status=TrackingStatus.IN_TRANSIT,
            )
        ]

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="stub-token")


class FailingCarrier(CarrierBase):
    """Raises an unexpected RuntimeError — simulates a programmer/infra error."""
    name = "failing"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number, carrier="failing", status=TrackingStatus.UNKNOWN
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise RuntimeError("Carrier API changed")

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="fail-token")


class TransientCarrier(CarrierBase):
    name = "transient"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        raise CarrierTransientError("transient", "connect timeout")

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        raise CarrierTransientError("transient", "connect timeout")

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="tok")


class AuthFailCarrier(CarrierBase):
    """Raises CarrierAuthError — simulates a genuine credential failure."""
    name = "authfail"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number, carrier="authfail", status=TrackingStatus.UNKNOWN
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        raise CarrierAuthError("authfail", "Password changed")

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        raise CarrierAuthError("authfail", "Invalid credentials")


@pytest.fixture
async def repo(tmp_path):
    r = PackageRepository(db_path=tmp_path / "test.db")
    await r.init()
    yield r
    await r.close()


async def test_poll_syncs_accounts_and_manual_packages(repo):
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    # Add a connected account
    await service.connect_account_credentials(
        "stub", "user", "pass", lookback_days=30
    )

    # Add a manual package (this itself fetches once, immediately)
    await service.add_package(tracking_number="MANUAL1", carrier="stub")
    assert carrier.track_count == 1

    await scheduler._poll_all()

    # Account was synced
    assert carrier.sync_count == 1
    # Manual package was refreshed again by the poll cycle
    assert carrier.track_count == 2


async def test_poll_skips_sync_disabled_accounts(repo):
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    account_id = await repo.add_account(
        carrier="stub", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )
    await repo.update_account_sync_enabled(account_id, False)

    await scheduler._poll_all()
    assert carrier.sync_count == 0


async def test_poll_skips_auth_failed_accounts(repo):
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    account_id = await repo.add_account(
        carrier="stub", auth_type="credentials",
        tokens={"access_token": "old"}, username="user",
    )
    await repo.update_account_status(
        account_id, "auth_failed", "Login flow changed"
    )

    await scheduler._poll_all()
    # Should NOT have tried to sync
    assert carrier.sync_count == 0


async def test_poll_handles_sync_failure_gracefully(repo):
    """An unexpected RuntimeError from a carrier does NOT flip the account to
    auth_failed — it's treated as a transient failure so the account stays
    connected and retries on the next cycle."""
    carrier = FailingCarrier()
    service = TrackingService(repository=repo, carriers={"failing": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    account_id = await repo.add_account(
        carrier="failing", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )

    await scheduler._poll_all()

    account = await repo.get_account(account_id)
    assert account["status"] == "connected"


async def test_poll_marks_auth_failed_on_carrier_auth_error(repo):
    """A genuine CarrierAuthError flips the account to auth_failed."""
    carrier = AuthFailCarrier()
    service = TrackingService(repository=repo, carriers={"authfail": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    account_id = await repo.add_account(
        carrier="authfail", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )

    await scheduler._poll_all()

    account = await repo.get_account(account_id)
    assert account["status"] == "auth_failed"
    assert "login flow may have changed" in account["status_message"]


async def test_poll_creates_auth_failure_notification(repo):
    """When a CarrierAuthError fires, a notification is created for the user."""
    carrier = AuthFailCarrier()
    service = TrackingService(repository=repo, carriers={"authfail": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    await repo.add_account(
        carrier="authfail", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )

    await scheduler._poll_all()

    notifications = await repo.list_notifications()
    assert len(notifications) == 1
    assert notifications[0]["carrier"] == "authfail"
    assert notifications[0]["old_status"] == "connected"
    assert notifications[0]["new_status"] == "auth_failed"
    assert notifications[0]["package_id"] is None
    assert notifications[0]["tracking_number"] == "Account"


async def test_poll_does_not_duplicate_auth_failure_notification(repo):
    """Repeated auth failures should NOT create a notification every time.

    Only the first failure creates one.  A new notification is created only
    after a successful sync clears the streak.
    """
    carrier = FailingCarrier()
    service = TrackingService(repository=repo, carriers={"failing": carrier})

    await repo.add_account(
        carrier="failing", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )

    # First failure — notification created
    await service.notify_auth_failure("failing", "Cookies expired")
    notifications = await repo.list_notifications()
    assert len(notifications) == 1

    # Second failure — suppressed (last notification for carrier is auth_failed)
    await service.notify_auth_failure("failing", "Still broken")
    notifications = await repo.list_notifications()
    assert len(notifications) == 1  # still just 1

    # Simulate a successful sync creating a status-change notification
    pkg_id = await repo.add_package(
        tracking_number="PKG1", carrier="failing",
    )
    await repo.add_notification(
        package_id=pkg_id, old_status="unknown", new_status="in_transit",
        tracking_number="PKG1", carrier="failing",
    )

    # Third failure AFTER a successful sync — notification created again
    await service.notify_auth_failure("failing", "Broken again")
    notifications = await repo.list_notifications()
    assert len(notifications) == 3  # original + status change + new auth failure
    assert notifications[0]["new_status"] == "auth_failed"


async def test_cloudflare_challenge_notification_deduped(repo):
    """Repeated Cloudflare challenges create only one notification until cleared."""
    service = TrackingService(repository=repo, carriers={})

    assert await service.notify_cloudflare_challenge("dpd") is True
    assert await service.notify_cloudflare_challenge("dpd") is False
    notifications = await repo.list_notifications()
    assert len(notifications) == 1
    assert notifications[0]["new_status"] == "cloudflare_challenge"

    # A successful sync (status-change notification) clears the streak
    pkg_id = await repo.add_package(tracking_number="PKG1", carrier="dpd")
    await repo.add_notification(
        package_id=pkg_id, old_status="unknown", new_status="in_transit",
        tracking_number="PKG1", carrier="dpd",
    )
    assert await service.notify_cloudflare_challenge("dpd") is True


async def test_poll_handles_empty(repo):
    service = TrackingService(repository=repo, carriers={})
    scheduler = PackageScheduler(tracking_service=service)
    await scheduler._poll_all()  # Should not raise


# --- Staleness / skip tests ---

async def test_poll_skips_delivered_package_older_than_14_days(repo):
    from datetime import UTC, datetime, timedelta
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    pkg_id = await repo.add_package(tracking_number="OLD1", carrier="stub")
    old_ts = (datetime.now(UTC) - timedelta(days=15)).isoformat()
    await repo.update_status(pkg_id, "delivered")
    # Backdate updated_at so it looks 15 days old
    await repo.db.execute(
        "UPDATE packages SET updated_at = ? WHERE id = ?", (old_ts, pkg_id)
    )
    await repo.db.commit()

    await scheduler._poll_all()
    assert carrier.track_count == 0


async def test_poll_does_not_skip_delivered_package_within_14_days(repo):
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    pkg_id = await repo.add_package(tracking_number="NEW1", carrier="stub")
    await repo.update_status(pkg_id, "delivered")

    await scheduler._poll_all()
    assert carrier.track_count == 1


async def test_poll_skips_package_with_max_consecutive_failures_within_reprobe_window(repo):
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    pkg_id = await repo.add_package(tracking_number="FAIL1", carrier="stub")
    now = datetime.now(UTC).isoformat()
    await repo.db.execute(
        "UPDATE packages SET consecutive_failures = 5, last_refreshed_at = ? WHERE id = ?",
        (now, pkg_id),
    )
    await repo.db.commit()

    await scheduler._poll_all()
    assert carrier.track_count == 0


async def test_poll_reprobes_stale_package_after_reprobe_window(repo):
    carrier = StubCarrier()
    service = TrackingService(repository=repo, carriers={"stub": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    pkg_id = await repo.add_package(tracking_number="FAIL1", carrier="stub")
    stale = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    await repo.db.execute(
        "UPDATE packages SET consecutive_failures = 5, last_refreshed_at = ? WHERE id = ?",
        (stale, pkg_id),
    )
    await repo.db.commit()

    await scheduler._poll_all()
    assert carrier.track_count == 1


async def test_consecutive_failures_incremented_on_transient_error(repo):
    service = TrackingService(repository=repo, carriers={"transient": TransientCarrier()})
    pkg_id = await repo.add_package(tracking_number="TRK1", carrier="transient")

    await service.refresh_package(pkg_id)

    pkg = await repo.get_package(pkg_id)
    assert pkg["consecutive_failures"] == 1


async def test_consecutive_failures_reset_on_success(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    pkg_id = await repo.add_package(tracking_number="RST1", carrier="stub")
    await repo.db.execute(
        "UPDATE packages SET consecutive_failures = 3 WHERE id = ?", (pkg_id,)
    )
    await repo.db.commit()

    await service.refresh_package(pkg_id)

    pkg = await repo.get_package(pkg_id)
    assert pkg["consecutive_failures"] == 0


# --- Garbage collection ---

async def _make_stale_unknown(repo, tracking_number, *, source="account",
                              failures=5, age_days=20, events=False):
    pkg_id = await repo.add_package(
        tracking_number=tracking_number, carrier="stub", source=source,
    )
    old_ts = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    await repo.db.execute(
        "UPDATE packages SET consecutive_failures = ?, created_at = ? WHERE id = ?",
        (failures, old_ts, pkg_id),
    )
    if events:
        await repo.add_event(
            package_id=pkg_id, timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            status="pre_transit", description="Registered",
        )
    await repo.db.commit()
    return pkg_id


async def test_gc_prunes_stale_unknown_account_package(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)
    pkg_id = await _make_stale_unknown(repo, "ORPHAN-1")

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is None


async def test_gc_prunes_stale_unknown_manual_package(repo):
    """User picked 'Both' — a manually-added number stuck at unknown is pruned too."""
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)
    pkg_id = await _make_stale_unknown(repo, "TYPO-1", source="manual")

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is None


async def test_gc_keeps_recent_unknown(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)
    pkg_id = await _make_stale_unknown(repo, "FRESH-1", age_days=3)

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is not None


async def test_gc_keeps_unknown_below_failure_threshold(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)
    pkg_id = await _make_stale_unknown(repo, "MAYBE-1", failures=3)

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is not None


async def test_gc_keeps_unknown_with_events(repo):
    """UNKNOWN but with real scan history — could be a status-mapping gap, not
    garbage. Left alone so it stays visible."""
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)
    pkg_id = await _make_stale_unknown(repo, "HASEVENTS-1", events=True)

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is not None


async def test_gc_prunes_old_delivered_and_cascades(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)

    pkg_id = await repo.add_package(tracking_number="OLD-DEL-1", carrier="stub")
    await repo.update_status(pkg_id, "delivered")
    await repo.add_event(
        package_id=pkg_id, timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        status="delivered", description="Delivered",
    )
    await repo.add_notification(
        package_id=pkg_id, old_status="in_transit", new_status="delivered",
        tracking_number="OLD-DEL-1", carrier="stub", label=None,
    )
    old_ts = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    await repo.db.execute(
        "UPDATE packages SET updated_at = ? WHERE id = ?", (old_ts, pkg_id)
    )
    await repo.db.commit()

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is None
    assert await repo.get_events(pkg_id) == []
    rows = await (await repo.db.execute(
        "SELECT COUNT(*) FROM notifications WHERE package_id = ?", (pkg_id,)
    )).fetchone()
    assert rows[0] == 0


async def test_gc_keeps_recent_delivered(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)

    pkg_id = await repo.add_package(tracking_number="NEW-DEL-1", carrier="stub")
    await repo.update_status(pkg_id, "delivered")
    ts = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    await repo.db.execute(
        "UPDATE packages SET updated_at = ? WHERE id = ?", (ts, pkg_id)
    )
    await repo.db.commit()

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is not None


async def test_gc_delivered_days_zero_disables_history_sweep(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service, gc_delivered_days=0)

    pkg_id = await repo.add_package(tracking_number="ANCIENT-1", carrier="stub")
    await repo.update_status(pkg_id, "delivered")
    old_ts = (datetime.now(UTC) - timedelta(days=999)).isoformat()
    await repo.db.execute(
        "UPDATE packages SET updated_at = ? WHERE id = ?", (old_ts, pkg_id)
    )
    await repo.db.commit()

    await scheduler._collect_garbage()

    assert await repo.get_package(pkg_id) is not None


async def test_gc_leaves_active_packages_alone(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})
    scheduler = PackageScheduler(tracking_service=service)

    in_transit = await repo.add_package(tracking_number="MOVING-1", carrier="stub")
    await repo.update_status(in_transit, "in_transit")
    old_ts = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    await repo.db.execute(
        "UPDATE packages SET updated_at = ?, created_at = ? WHERE id = ?",
        (old_ts, old_ts, in_transit),
    )
    await repo.db.commit()

    await scheduler._collect_garbage()

    assert await repo.get_package(in_transit) is not None


async def test_gc_job_registered_only_when_enabled(repo):
    service = TrackingService(repository=repo, carriers={"stub": StubCarrier()})

    on = PackageScheduler(tracking_service=service, gc_enabled=True)
    on.start()
    try:
        assert on._scheduler.get_job("collect_garbage") is not None
    finally:
        on.stop()

    off = PackageScheduler(tracking_service=service, gc_enabled=False)
    off.start()
    try:
        assert off._scheduler.get_job("collect_garbage") is None
    finally:
        off.stop()
