from datetime import UTC, datetime

import pytest

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierAuthError,
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
                    timestamp=datetime(2026, 4, 11, 10, 0, tzinfo=UTC),
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


# --- validate_account_* tests ---


class FailingCredCarrier(CarrierBase):
    name = "fail-cred"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        raise RuntimeError("invalid password")


class FailingTokenCarrier(CarrierBase):
    name = "fail-token"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        raise RuntimeError("token expired")


async def test_validate_credentials_success_returns_tokens_without_persisting(service: TrackingService, repo):
    tokens = await service.validate_account_credentials("stub", "user@test.com", "pw")

    assert tokens.access_token == "stub-token"
    accounts = await repo.list_accounts()
    assert accounts == []


async def test_validate_credentials_failure_raises_carrier_auth_error(repo):
    service = TrackingService(repository=repo, carriers={"fail-cred": FailingCredCarrier()})

    with pytest.raises(CarrierAuthError) as exc_info:
        await service.validate_account_credentials("fail-cred", "u", "p")

    assert exc_info.value.carrier == "fail-cred"
    assert "invalid password" in exc_info.value.message


async def test_validate_credentials_unknown_carrier(service: TrackingService):
    with pytest.raises(ValueError, match="Unknown carrier"):
        await service.validate_account_credentials("nope", "u", "p")


async def test_validate_credentials_wrong_auth_type(repo):
    service = TrackingService(
        repository=repo, carriers={"fail-token": FailingTokenCarrier()},
    )
    with pytest.raises(ValueError, match="does not use credentials"):
        await service.validate_account_credentials("fail-token", "u", "p")


async def test_validate_manual_token_success_does_not_persist(service: TrackingService, repo):
    tokens = await service.validate_account_manual_token("stub", "tok-1", "refresh-1")

    assert tokens.access_token == "tok-1"
    assert tokens.refresh_token == "refresh-1"
    assert await repo.list_accounts() == []


async def test_validate_manual_token_failure_raises_carrier_auth_error(repo):
    service = TrackingService(repository=repo, carriers={"fail-token": FailingTokenCarrier()})

    with pytest.raises(CarrierAuthError) as exc_info:
        await service.validate_account_manual_token("fail-token", "bad-token")

    assert exc_info.value.carrier == "fail-token"
    assert "token expired" in exc_info.value.message


async def test_validate_manual_token_unknown_carrier(service: TrackingService):
    with pytest.raises(ValueError, match="Unknown carrier"):
        await service.validate_account_manual_token("nope", "tok")


async def test_connect_credentials_persists_after_validation(service: TrackingService, repo):
    account = await service.connect_account_credentials("stub", "u@t.com", "pw")

    assert account["carrier"] == "stub"
    accounts = await repo.list_accounts()
    assert len(accounts) == 1


async def test_connect_manual_token_fails_when_token_invalid(repo):
    service = TrackingService(repository=repo, carriers={"fail-token": FailingTokenCarrier()})

    with pytest.raises(CarrierAuthError):
        await service.connect_account_manual_token("fail-token", "bad")

    # Failed connect must not persist a broken account
    assert await repo.list_accounts() == []


class ManualTokenStub(CarrierBase):
    name = "mt-stub"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []


async def test_update_manual_token_preserves_username(repo):
    """Token refresh from the extension must not wipe the stored username.

    Regression guard: PostNL (and future extension-driven carriers) persist
    ``username`` on the account row; prior implementation passed
    ``username=None`` on token refresh which NULL-ed that column.
    """
    service = TrackingService(repository=repo, carriers={"mt-stub": ManualTokenStub()})

    account_id = await repo.add_account(
        carrier="mt-stub", auth_type="browser_push",
        tokens={"access_token": "old", "refresh_token": None},
        username="remembered@example.com",
    )

    await service.update_account_manual_token(
        account_id, "mt-stub", "refreshed-token",
    )

    account = await repo.get_account(account_id)
    assert account is not None
    assert account["username"] == "remembered@example.com"
    assert (account["tokens"] or {}).get("access_token") == "refreshed-token"


async def test_update_manual_token_preserves_existing_refresh_token(repo):
    """Token refresh without a new refresh_token must keep the stored one.

    Regression guard: older extension versions PATCHed /accounts/{id}/token
    with only ``access_token``. The previous implementation overwrote
    ``refresh_token`` with ``None`` — wiping the credentials JSON that
    PostNL and other extension-driven carriers rely on for auto-login on
    the next sync. Recovery required the user to re-enter their password.
    """
    service = TrackingService(repository=repo, carriers={"mt-stub": ManualTokenStub()})

    stored_creds = '{"email": "u@example.com", "password": "p"}'
    account_id = await repo.add_account(
        carrier="mt-stub", auth_type="browser_push",
        tokens={"access_token": "old", "refresh_token": stored_creds},
        username="u@example.com",
    )

    # Caller passes refresh_token=None — emulates a PATCH body with only
    # ``access_token`` (the old extension's sync flow).
    await service.update_account_manual_token(
        account_id, "mt-stub", "refreshed-token", refresh_token=None,
    )

    account = await repo.get_account(account_id)
    assert account is not None
    tokens = account["tokens"] or {}
    assert tokens.get("access_token") == "refreshed-token"
    assert tokens.get("refresh_token") == stored_creds


class ExtensionTokenStub(CarrierBase):
    name = "ext-stub"
    auth_type = AuthType.EXTENSION_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return [
            TrackingResult(
                tracking_number="EXT-001",
                carrier=self.name,
                status=TrackingStatus.IN_TRANSIT,
                events=[
                    TrackingEvent(
                        timestamp=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
                        status=TrackingStatus.IN_TRANSIT,
                        description="Onderweg",
                    ),
                ],
            ),
        ]


async def test_sync_account_calls_sync_packages_for_extension_token(repo):
    """EXTENSION_TOKEN carriers (PostNL) must hit sync_packages server-side.

    Regression guard: the BROWSER_PUSH short-circuit in ``sync_account``
    previously swallowed PostNL too, causing the Chrome extension to
    always report "0 packages synced."
    """
    service = TrackingService(repository=repo, carriers={"ext-stub": ExtensionTokenStub()})
    account_id = await repo.add_account(
        carrier="ext-stub", auth_type="extension_token",
        tokens={"access_token": "bearer-xyz"},
        username="u@example.com",
    )

    results = await service.sync_account(account_id)

    assert len(results) == 1
    assert results[0]["tracking_number"] == "EXT-001"
    assert results[0]["current_status"] == "in_transit"
