import pytest
from httpx import ASGITransport, AsyncClient

from dwmp.api.app import create_app
from dwmp.api.dependencies import get_repository, get_tracking_service
from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingResult,
    TrackingStatus,
)
from dwmp.storage.repository import PackageRepository
from dwmp.services.tracking import TrackingService


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


class StubCredCarrier(CarrierBase):
    name = "dpd"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN
        )

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []

    async def login(self, username: str, password: str) -> AuthTokens:
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
