from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from dwmp.api.app import create_app
from dwmp.api.dependencies import get_repository, get_tracking_service
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


class StubPostNLCarrier(CarrierBase):
    name = "postnl"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN
        )

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return [
            TrackingResult(tracking_number="SYNCED-1", carrier=self.name, status=TrackingStatus.IN_TRANSIT),
        ]

    def _parse_browser_payload(
        self, payload: dict, lookback_days: int = 30
    ) -> list[TrackingResult]:
        if payload.get("mode") == "error":
            raise RuntimeError("bad payload")
        return [
            TrackingResult(
                tracking_number="BROWSER-1",
                carrier=self.name,
                status=TrackingStatus.OUT_FOR_DELIVERY,
                postal_code="1234AB",
                tracking_url="https://jouw.postnl.nl/track-and-trace/BROWSER-1-NL-1234AB",
                events=[
                    TrackingEvent(
                        timestamp=datetime(2026, 4, 21, 9, 58, tzinfo=UTC),
                        status=TrackingStatus.OUT_FOR_DELIVERY,
                        description="Bezorger is onderweg",
                    ),
                ],
            ),
        ]


class StubCredCarrier(CarrierBase):
    name = "dpd"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN
        )

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="dpd-token")


@pytest.fixture
async def repo(tmp_path):
    r = PackageRepository(db_path=tmp_path / "test.db")
    await r.init()
    yield r
    await r.close()


@pytest.fixture
def app(repo):
    application = create_app()
    service = TrackingService(
        repository=repo,
        carriers={"postnl": StubPostNLCarrier(), "dpd": StubCredCarrier()},
    )
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_tracking_service] = lambda: service
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Package CRUD tests ---

async def test_add_package(client: AsyncClient):
    response = await client.post(
        "/api/v1/packages",
        json={
            "tracking_number": "3STEST123456",
            "carrier": "postnl",
            "label": "New headphones",
            "postal_code": "1234AB",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_number"] == "3STEST123456"
    assert data["source"] == "manual"


async def test_add_duplicate_package_returns_409(client: AsyncClient):
    payload = {"tracking_number": "DUP1", "carrier": "dpd"}
    await client.post("/api/v1/packages", json=payload)
    response = await client.post("/api/v1/packages", json=payload)
    assert response.status_code == 409


async def test_list_packages(client: AsyncClient):
    await client.post("/api/v1/packages", json={"tracking_number": "A", "carrier": "postnl"})
    await client.post("/api/v1/packages", json={"tracking_number": "B", "carrier": "dpd"})

    response = await client.get("/api/v1/packages")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_package(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/packages",
        json={"tracking_number": "GET1", "carrier": "dpd"},
    )
    pkg_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/packages/{pkg_id}")
    assert response.status_code == 200
    assert response.json()["tracking_number"] == "GET1"
    assert "events" in response.json()


async def test_get_nonexistent_package_returns_404(client: AsyncClient):
    response = await client.get("/api/v1/packages/999")
    assert response.status_code == 404


async def test_delete_package(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/packages",
        json={"tracking_number": "DEL1", "carrier": "postnl"},
    )
    pkg_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/packages/{pkg_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/v1/packages/{pkg_id}")
    assert get_resp.status_code == 404


async def test_delete_nonexistent_returns_404(client: AsyncClient):
    response = await client.delete("/api/v1/packages/999")
    assert response.status_code == 404


async def test_refresh_nonexistent_returns_404(client: AsyncClient):
    response = await client.post("/api/v1/packages/999/refresh")
    assert response.status_code == 404


# --- Carrier list tests ---

async def test_list_carriers_with_auth_type(client: AsyncClient):
    response = await client.get("/api/v1/carriers")
    assert response.status_code == 200
    carriers = response.json()
    names = {c["name"] for c in carriers}
    assert names == {"postnl", "dpd"}
    postnl = next(c for c in carriers if c["name"] == "postnl")
    assert postnl["auth_type"] == "manual_token"
    assert "auth_hint" in postnl


# --- Account tests ---

async def test_manual_token_creates_postnl_account(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/token",
        json={
            "carrier": "postnl",
            "access_token": "my-postnl-token",
            "refresh_token": "my-refresh",
            "lookback_days": 14,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["carrier"] == "postnl"
    assert data["status"] == "connected"
    assert data["lookback_days"] == 14


async def test_credentials_creates_dpd_account(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/credentials",
        json={
            "carrier": "dpd",
            "username": "testuser",
            "password": "testpass",
            "lookback_days": 7,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["carrier"] == "dpd"
    assert data["status"] == "connected"


async def test_manual_token_creates_account(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/token",
        json={
            "carrier": "postnl",
            "access_token": "my-access-token",
            "refresh_token": "my-refresh-token",
            "lookback_days": 30,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["carrier"] == "postnl"
    assert data["status"] == "connected"
    assert data["auth_type"] == "manual_token"


async def test_manual_token_unknown_carrier(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "unknown", "access_token": "tok"},
    )
    assert response.status_code == 400


async def test_list_accounts_strips_tokens(client: AsyncClient):
    await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    response = await client.get("/api/v1/accounts")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert "tokens" not in response.json()[0]


async def test_delete_account(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    account_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/accounts/{account_id}")
    assert response.status_code == 204


async def test_sync_account(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    account_id = create_resp.json()["id"]

    response = await client.post(f"/api/v1/accounts/{account_id}/sync")
    assert response.status_code == 200
    packages = response.json()
    assert len(packages) == 1
    assert packages[0]["tracking_number"] == "SYNCED-1"
    assert packages[0]["source"] == "account"


async def test_browser_payload_sync_account(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    account_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/accounts/{account_id}/browser-payload",
        json={"payload": {"shipments": [], "details": []}},
    )
    assert response.status_code == 200
    packages = response.json()
    assert len(packages) == 1
    assert packages[0]["tracking_number"] == "BROWSER-1"
    assert packages[0]["current_status"] == "out_for_delivery"


async def test_browser_payload_sync_invalid_account_returns_400(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/999/browser-payload",
        json={"payload": {"shipments": [], "details": []}},
    )
    assert response.status_code == 400


# --- Notification endpoint tests ---


async def test_list_notifications_empty(client: AsyncClient):
    response = await client.get("/api/v1/notifications")
    assert response.status_code == 200
    assert response.json() == []


async def test_unread_count_zero(client: AsyncClient):
    response = await client.get("/api/v1/notifications/unread-count")
    assert response.status_code == 200
    assert response.json() == {"count": 0}


async def test_notifications_after_sync(client: AsyncClient):
    # Create account and sync to trigger status change (unknown -> in_transit)
    create_resp = await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    account_id = create_resp.json()["id"]
    await client.post(f"/api/v1/accounts/{account_id}/sync")

    # Check unread count
    count_resp = await client.get("/api/v1/notifications/unread-count")
    assert count_resp.json()["count"] == 1

    # Check notification list
    list_resp = await client.get("/api/v1/notifications")
    notifications = list_resp.json()
    assert len(notifications) == 1
    assert notifications[0]["tracking_number"] == "SYNCED-1"
    assert notifications[0]["new_status"] == "in_transit"


async def test_mark_notification_read(client: AsyncClient):
    # Create a notification via sync
    create_resp = await client.post(
        "/api/v1/accounts/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    account_id = create_resp.json()["id"]
    await client.post(f"/api/v1/accounts/{account_id}/sync")

    list_resp = await client.get("/api/v1/notifications")
    notifications = list_resp.json()
    assert len(notifications) == 1

    notif_id = notifications[0]["id"]
    response = await client.post(f"/api/v1/notifications/{notif_id}/read")
    assert response.status_code == 200

    count_resp = await client.get("/api/v1/notifications/unread-count")
    assert count_resp.json()["count"] == 0


async def test_mark_all_read(client: AsyncClient):
    response = await client.post("/api/v1/notifications/read-all")
    assert response.status_code == 200
    assert response.json() == {"marked": 0}


# --- Test-only "validate" endpoints ---


class FailingCredCarrier(CarrierBase):
    name = "amazon"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        raise RuntimeError("invalid credentials")


class FailingTokenCarrier(CarrierBase):
    name = "postnl"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        raise RuntimeError("token expired")


@pytest.fixture
def failing_app(repo):
    application = create_app()
    service = TrackingService(
        repository=repo,
        carriers={"amazon": FailingCredCarrier(), "postnl": FailingTokenCarrier()},
    )
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_tracking_service] = lambda: service
    return application


@pytest.fixture
async def failing_client(failing_app):
    transport = ASGITransport(app=failing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_test_credentials_success_does_not_persist(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/test/credentials",
        json={"carrier": "dpd", "username": "u", "password": "p"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # No account was persisted
    list_resp = await client.get("/api/v1/accounts")
    assert list_resp.json() == []


async def test_test_credentials_auth_failure_returns_502(failing_client: AsyncClient):
    response = await failing_client.post(
        "/api/v1/accounts/test/credentials",
        json={"carrier": "amazon", "username": "u", "password": "p"},
    )
    assert response.status_code == 502
    assert "invalid credentials" in response.json()["detail"]


async def test_test_credentials_unknown_carrier_returns_400(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/test/credentials",
        json={"carrier": "nope", "username": "u", "password": "p"},
    )
    assert response.status_code == 400


async def test_test_token_success_does_not_persist(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/test/token",
        json={"carrier": "postnl", "access_token": "tok"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    list_resp = await client.get("/api/v1/accounts")
    assert list_resp.json() == []


async def test_test_token_failure_returns_502(failing_client: AsyncClient):
    response = await failing_client.post(
        "/api/v1/accounts/test/token",
        json={"carrier": "postnl", "access_token": "bad"},
    )
    assert response.status_code == 502
    assert "token expired" in response.json()["detail"]


async def test_test_token_unknown_carrier_returns_400(client: AsyncClient):
    response = await client.post(
        "/api/v1/accounts/test/token",
        json={"carrier": "nope", "access_token": "tok"},
    )
    assert response.status_code == 400


# --- Extension auto-update endpoint ---


async def test_extension_updates_xml(client: AsyncClient):
    response = await client.get("/api/v1/extension/updates.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    body = response.text
    assert '<?xml version="1.0"' in body
    assert "gupdate" in body
    assert "updatecheck" in body
    assert ".crx" in body


async def test_extension_updates_xml_echoes_appid(client: AsyncClient):
    """Chrome sends the extension ID in the x query param."""
    response = await client.get(
        "/api/v1/extension/updates.xml",
        params={"x": "id=abcdefghijklmnop&v=1.0.0"},
    )
    assert response.status_code == 200
    assert 'appid="abcdefghijklmnop"' in response.text


async def test_extension_updates_xml_default_appid(client: AsyncClient):
    """Without the x param, uses a default appid."""
    response = await client.get("/api/v1/extension/updates.xml")
    assert response.status_code == 200
    assert 'appid="extension"' in response.text
