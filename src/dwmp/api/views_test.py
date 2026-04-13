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
from dwmp.services.tracking import TrackingService
from dwmp.storage.repository import PackageRepository


class StubAmazon(CarrierBase):
    name = "amazon"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="amazon-tok")


class StubPostNL(CarrierBase):
    name = "postnl"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []


class StubDPD(CarrierBase):
    name = "dpd"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []


class StubGLS(CarrierBase):
    name = "gls"
    auth_type = AuthType.MANUAL_TOKEN

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(tracking_number=tracking_number, carrier=self.name, status=TrackingStatus.UNKNOWN)

    async def sync_packages(self, tokens: AuthTokens, lookback_days: int = 30) -> list[TrackingResult]:
        return []


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
        carriers={
            "amazon": StubAmazon(), "postnl": StubPostNL(),
            "dpd": StubDPD(), "gls": StubGLS(),
        },
    )
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_tracking_service] = lambda: service
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_accounts_page_lists_carriers_with_add_buttons(client: AsyncClient):
    response = await client.get("/accounts")
    assert response.status_code == 200
    body = response.text
    assert "Add a Carrier" in body
    # Add buttons present for credential and manual_token carriers
    assert 'hx-get="/accounts/add/amazon"' in body
    assert 'hx-get="/accounts/add/postnl"' in body
    assert 'hx-get="/accounts/add/dpd"' in body
    # GLS is shown as no-account-needed, no Add button
    assert 'hx-get="/accounts/add/gls"' not in body
    assert "No account needed" in body


async def test_add_form_amazon_includes_totp_field(client: AsyncClient):
    response = await client.get("/accounts/add/amazon")
    assert response.status_code == 200
    body = response.text
    assert 'name="username"' in body
    assert 'name="password"' in body
    assert 'name="totp_secret"' in body
    assert "Test connection" in body


async def test_add_form_postnl_shows_session_storage_wizard(client: AsyncClient):
    response = await client.get("/accounts/add/postnl")
    assert response.status_code == 200
    body = response.text
    assert "Session Storage" in body
    assert "akamai:1e450c3d-5bbb-4f34-9264-dd51fa9fd066:oidc-tokens" in body
    assert 'name="access_token"' in body
    assert 'name="refresh_token"' in body


async def test_add_form_dpd_shows_console_snippet(client: AsyncClient):
    response = await client.get("/accounts/add/dpd")
    assert response.status_code == 200
    body = response.text
    assert "copy(document.documentElement.outerHTML)" in body
    assert 'name="access_token"' in body


async def test_add_form_gls_returns_404(client: AsyncClient):
    response = await client.get("/accounts/add/gls")
    assert response.status_code == 404


async def test_add_form_unknown_carrier_returns_404(client: AsyncClient):
    response = await client.get("/accounts/add/notreal")
    assert response.status_code == 404


async def test_add_form_cancel_returns_empty(client: AsyncClient):
    response = await client.get("/accounts/add/amazon/cancel")
    assert response.status_code == 200
    assert response.text == ""


async def test_test_credentials_endpoint_returns_ok_html(client: AsyncClient):
    response = await client.post(
        "/accounts/add/amazon/test",
        data={"username": "u", "password": "p"},
    )
    assert response.status_code == 200
    assert "test-result ok" in response.text
    assert "Connection works" in response.text


async def test_test_token_endpoint_returns_ok_html(client: AsyncClient):
    response = await client.post(
        "/accounts/add/postnl/test",
        data={"access_token": "tok", "refresh_token": "ref"},
    )
    assert response.status_code == 200
    assert "test-result ok" in response.text


async def test_save_credentials_triggers_hx_refresh(client: AsyncClient, repo):
    response = await client.post(
        "/accounts/add/amazon/save",
        data={"username": "u", "password": "p", "lookback_days": "30"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Refresh") == "true"

    accounts = await repo.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["carrier"] == "amazon"


async def test_save_token_triggers_hx_refresh(client: AsyncClient, repo):
    response = await client.post(
        "/accounts/add/postnl/save",
        data={"access_token": "tok", "lookback_days": "14"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Refresh") == "true"

    accounts = await repo.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["carrier"] == "postnl"
    assert accounts[0]["lookback_days"] == 14


async def test_sync_account_view_returns_refreshed_row(client: AsyncClient, repo):
    await client.post(
        "/accounts/add/amazon/save",
        data={"username": "u", "password": "p", "lookback_days": "30"},
    )
    account_id = (await repo.list_accounts())[0]["id"]

    response = await client.post(f"/accounts/{account_id}/sync")
    assert response.status_code == 200
    body = response.text
    assert f'id="account-{account_id}"' in body
    assert "Synced" in body
    # Must not leak the raw tokens dict into rendered HTML.
    assert "access_token" not in body


async def test_sync_account_view_missing_account_returns_404(client: AsyncClient):
    response = await client.post("/accounts/9999/sync")
    assert response.status_code == 404
