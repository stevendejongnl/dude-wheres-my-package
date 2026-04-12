from datetime import datetime

import pytest

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)


class FakeOAuthCarrier(CarrierBase):
    name = "fake_oauth"
    auth_type = AuthType.OAUTH

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=TrackingStatus.IN_TRANSIT,
            events=[
                TrackingEvent(
                    timestamp=datetime(2026, 4, 11, 10, 0),
                    status=TrackingStatus.IN_TRANSIT,
                    description="Package is on its way",
                    location="Amsterdam",
                ),
            ],
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        return [await self.track("SYNCED-001")]

    async def get_auth_url(self, callback_url: str) -> str:
        return f"https://fake.auth/login?redirect={callback_url}"

    async def handle_callback(self, code: str, callback_url: str) -> AuthTokens:
        return AuthTokens(access_token="fake-token", refresh_token="fake-refresh")


class FakeCredentialCarrier(CarrierBase):
    name = "fake_cred"
    auth_type = AuthType.CREDENTIALS

    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        return TrackingResult(
            tracking_number=tracking_number,
            carrier=self.name,
            status=TrackingStatus.DELIVERED,
        )

    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        return [await self.track("SYNCED-002")]

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        return AuthTokens(access_token="cred-token")


async def test_oauth_carrier_contract():
    carrier = FakeOAuthCarrier()
    assert carrier.auth_type == AuthType.OAUTH

    url = await carrier.get_auth_url("http://localhost/callback")
    assert "fake.auth" in url

    tokens = await carrier.handle_callback("code123", "http://localhost/callback")
    assert tokens.access_token == "fake-token"

    packages = await carrier.sync_packages(tokens)
    assert len(packages) == 1
    assert packages[0].tracking_number == "SYNCED-001"


async def test_credential_carrier_contract():
    carrier = FakeCredentialCarrier()
    assert carrier.auth_type == AuthType.CREDENTIALS

    tokens = await carrier.login("user", "pass")
    assert tokens.access_token == "cred-token"

    packages = await carrier.sync_packages(tokens)
    assert len(packages) == 1


async def test_manual_track_still_works():
    carrier = FakeOAuthCarrier()
    result = await carrier.track("MANUAL123")
    assert result.tracking_number == "MANUAL123"
    assert result.status == TrackingStatus.IN_TRANSIT


def test_tracking_status_values():
    assert TrackingStatus.DELIVERED == "delivered"
    assert TrackingStatus.IN_TRANSIT == "in_transit"


def test_auth_type_values():
    assert AuthType.OAUTH == "oauth"
    assert AuthType.CREDENTIALS == "credentials"


def test_tracking_event_is_immutable():
    event = TrackingEvent(
        timestamp=datetime(2026, 4, 11),
        status=TrackingStatus.DELIVERED,
        description="Delivered",
    )
    assert event.location is None


async def test_oauth_carrier_rejects_login():
    carrier = FakeOAuthCarrier()
    with pytest.raises(NotImplementedError):
        await carrier.login("user", "pass")


async def test_credential_carrier_rejects_oauth():
    carrier = FakeCredentialCarrier()
    with pytest.raises(NotImplementedError):
        await carrier.get_auth_url("http://callback")
