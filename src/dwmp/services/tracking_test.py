import pytest
from datetime import datetime, timezone

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)
from dwmp.services.tracking import TrackingService
from dwmp.storage.repository import PackageRepository


class StubCarrier(CarrierBase):
    name = "stub"
    auth_type = AuthType.CREDENTIALS

    def __init__(self, result: TrackingResult | None = None) -> None:
        self._result = result or TrackingResult(
            tracking_number="",
            carrier="stub",
            status=TrackingStatus.IN_TRANSIT,
            events=[
                TrackingEvent(
                    timestamp=datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc),
                    status=TrackingStatus.IN_TRANSIT,
                    description="On its way",
                    location="Amsterdam",
                ),
            ],
        )

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self._result.carrier,
            status=self._result.status,
            estimated_delivery=self._result.estimated_delivery,
            events=self._result.events,
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        return [
            TrackingResult(
                tracking_number="SYNCED-001",
                carrier="stub",
                status=TrackingStatus.IN_TRANSIT,
            )
        ]

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="stub-token")


@pytest.fixture
async def repo(tmp_path):
    r = PackageRepository(db_path=tmp_path / "test.db")
    await r.init()
    yield r
    await r.close()


@pytest.fixture
def service(repo):
    return TrackingService(
        repository=repo,
        carriers={"stub": StubCarrier()},
    )


async def test_list_carriers(service: TrackingService):
    assert service.list_carriers() == ["stub"]


async def test_add_and_get_package(service: TrackingService):
    pkg = await service.add_package(
        tracking_number="TEST1", carrier="stub", label="Test"
    )
    assert pkg["tracking_number"] == "TEST1"

    full = await service.get_package(pkg["id"])
    assert full is not None
    assert full["events"] == []


async def test_refresh_package_updates_status(service: TrackingService):
    pkg = await service.add_package(tracking_number="REF1", carrier="stub")
    assert pkg["current_status"] == "unknown"

    refreshed = await service.refresh_package(pkg["id"])
    assert refreshed is not None
    assert refreshed["current_status"] == "in_transit"
    assert len(refreshed["events"]) == 1
    assert refreshed["events"][0]["description"] == "On its way"


async def test_refresh_unknown_carrier_returns_package(repo):
    service = TrackingService(repository=repo, carriers={})
    pkg = await service.add_package(tracking_number="UNK1", carrier="nocarrier")
    result = await service.refresh_package(pkg["id"])
    assert result is not None
    assert result["current_status"] == "unknown"


async def test_refresh_nonexistent_returns_none(service: TrackingService):
    result = await service.refresh_package(999)
    assert result is None


# --- Notification tests ---


async def test_refresh_creates_notification_on_status_change(service: TrackingService):
    pkg = await service.add_package(tracking_number="NCHG1", carrier="stub")
    assert pkg["current_status"] == "unknown"

    await service.refresh_package(pkg["id"])

    notifications = await service.list_notifications()
    assert len(notifications) == 1
    assert notifications[0]["old_status"] == "unknown"
    assert notifications[0]["new_status"] == "in_transit"
    assert notifications[0]["tracking_number"] == "NCHG1"


async def test_refresh_no_notification_when_status_unchanged(service: TrackingService):
    pkg = await service.add_package(tracking_number="NSAME1", carrier="stub")

    # First refresh: unknown -> in_transit (creates notification)
    await service.refresh_package(pkg["id"])
    assert len(await service.list_notifications()) == 1

    # Second refresh: in_transit -> in_transit (no new notification)
    await service.refresh_package(pkg["id"])
    assert len(await service.list_notifications()) == 1


async def test_sync_creates_notification_on_status_change(service: TrackingService, repo):
    account_id = await repo.add_account(
        carrier="stub", auth_type="credentials",
        tokens={"access_token": "tok"}, username="user@test.com",
    )

    await service.sync_account(account_id)

    # Synced package went from unknown -> in_transit
    notifications = await service.list_notifications()
    assert len(notifications) == 1
    assert notifications[0]["new_status"] == "in_transit"


async def test_mark_notification_read(service: TrackingService):
    pkg = await service.add_package(tracking_number="MREAD1", carrier="stub")
    await service.refresh_package(pkg["id"])

    assert await service.get_unread_notification_count() == 1

    notifications = await service.list_notifications()
    await service.mark_notification_read(notifications[0]["id"])

    assert await service.get_unread_notification_count() == 0


async def test_mark_all_notifications_read(service: TrackingService):
    pkg1 = await service.add_package(tracking_number="MALL1", carrier="stub")
    await service.refresh_package(pkg1["id"])

    assert await service.get_unread_notification_count() == 1

    count = await service.mark_all_notifications_read()
    assert count == 1
    assert await service.get_unread_notification_count() == 0
