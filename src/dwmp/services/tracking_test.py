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

    async def login(self, username: str, password: str) -> AuthTokens:
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
