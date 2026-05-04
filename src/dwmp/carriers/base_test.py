from datetime import datetime

from dwmp.carriers.base import (
    AuthTokens,
    AuthType,
    CarrierBase,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)


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


async def test_credential_carrier_contract():
    carrier = FakeCredentialCarrier()
    assert carrier.auth_type == AuthType.CREDENTIALS

    tokens = await carrier.login("user", "pass")
    assert tokens.access_token == "cred-token"

    packages = await carrier.sync_packages(tokens)
    assert len(packages) == 1


def test_tracking_status_values():
    assert TrackingStatus.DELIVERED == "delivered"
    assert TrackingStatus.IN_TRANSIT == "in_transit"


def test_auth_type_values():
    assert AuthType.CREDENTIALS == "credentials"
    assert AuthType.MANUAL_TOKEN == "manual_token"
    assert AuthType.BROWSER_PUSH == "browser_push"
    assert AuthType.EXTENSION_TOKEN == "extension_token"


def test_tracking_event_is_immutable():
    event = TrackingEvent(
        timestamp=datetime(2026, 4, 11),
        status=TrackingStatus.DELIVERED,
        description="Delivered",
    )
    assert event.location is None


