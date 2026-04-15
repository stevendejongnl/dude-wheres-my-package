import pytest

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
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

    # Add a manual package
    await service.add_package(tracking_number="MANUAL1", carrier="stub")

    await scheduler._poll_all()

    # Account was synced
    assert carrier.sync_count == 1
    # Manual package was refreshed
    assert carrier.track_count == 1


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
    """When a carrier's API fails, the account is marked auth_failed
    and a descriptive error message is stored."""
    carrier = FailingCarrier()
    service = TrackingService(repository=repo, carriers={"failing": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    account_id = await repo.add_account(
        carrier="failing", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )

    await scheduler._poll_all()

    account = await repo.get_account(account_id)
    assert account["status"] == "auth_failed"
    assert "login flow may have changed" in account["status_message"]


async def test_poll_creates_auth_failure_notification(repo):
    """When sync fails, a notification is created so the user sees it."""
    carrier = FailingCarrier()
    service = TrackingService(repository=repo, carriers={"failing": carrier})
    scheduler = PackageScheduler(tracking_service=service)

    await repo.add_account(
        carrier="failing", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user",
    )

    await scheduler._poll_all()

    notifications = await repo.list_notifications()
    assert len(notifications) == 1
    assert notifications[0]["carrier"] == "failing"
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


async def test_poll_handles_empty(repo):
    service = TrackingService(repository=repo, carriers={})
    scheduler = PackageScheduler(tracking_service=service)
    await scheduler._poll_all()  # Should not raise
