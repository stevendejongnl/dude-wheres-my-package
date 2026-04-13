from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TrackingStatus(StrEnum):
    UNKNOWN = "unknown"
    PRE_TRANSIT = "pre_transit"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    FAILED_ATTEMPT = "failed_attempt"
    RETURNED = "returned"
    EXCEPTION = "exception"


class AuthType(StrEnum):
    OAUTH = "oauth"
    CREDENTIALS = "credentials"
    MANUAL_TOKEN = "manual_token"


class AccountStatus(StrEnum):
    CONNECTED = "connected"
    AUTH_FAILED = "auth_failed"
    ERROR = "error"


class CarrierAuthError(Exception):
    """Raised when carrier auth fails — login flow changed, tokens expired, or credentials invalid.

    The service catches this and marks the account as auth_failed with a user-friendly message
    suggesting to re-authenticate or check for carrier API changes.
    """

    def __init__(self, carrier: str, message: str) -> None:
        self.carrier = carrier
        self.message = message
        super().__init__(f"[{carrier}] Auth failed: {message}")


@dataclass(frozen=True)
class TrackingEvent:
    timestamp: datetime
    status: TrackingStatus
    description: str
    location: str | None = None


@dataclass(frozen=True)
class TrackingResult:
    tracking_number: str
    carrier: str
    status: TrackingStatus
    estimated_delivery: datetime | None = None
    events: list[TrackingEvent] = field(default_factory=list)


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    # Optional user-agent the cookies/session were issued to. Used by browser
    # carriers (DPD) so headless replay matches the fingerprint Cloudflare
    # bound cf_clearance to. Safely ignored by carriers that don't need it.
    user_agent: str | None = None


class CarrierBase(ABC):
    name: str
    auth_type: AuthType

    @abstractmethod
    async def track(self, tracking_number: str, **kwargs: str) -> TrackingResult:
        """Fetch tracking info for a single package (manual tracking)."""

    @abstractmethod
    async def sync_packages(
        self, tokens: AuthTokens, lookback_days: int = 30
    ) -> list[TrackingResult]:
        """Fetch all packages from an authenticated account."""

    async def get_auth_url(self, callback_url: str) -> str:
        """Return OAuth redirect URL. Only for AuthType.OAUTH carriers."""
        raise NotImplementedError(f"{self.name} does not use OAuth")

    async def handle_callback(self, code: str, callback_url: str) -> AuthTokens:
        """Exchange OAuth code for tokens. Only for AuthType.OAUTH carriers."""
        raise NotImplementedError(f"{self.name} does not use OAuth")

    async def login(self, username: str, password: str, **kwargs: str) -> AuthTokens:
        """Authenticate with credentials. Only for AuthType.CREDENTIALS carriers."""
        raise NotImplementedError(f"{self.name} does not use credentials")

    def get_updated_tokens(self) -> AuthTokens | None:
        """Return updated tokens if the last sync refreshed them (e.g. browser cookies)."""
        return None

    async def refresh_tokens(self, tokens: AuthTokens) -> AuthTokens:
        """Refresh expired tokens. Returns new tokens."""
        raise NotImplementedError(f"{self.name} does not support token refresh")
