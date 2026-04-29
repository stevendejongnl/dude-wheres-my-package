from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def no_date_fallback() -> datetime:
    """Stable fallback timestamp for events with no parseable date.

    Uses start-of-day so repeated syncs on the same day produce
    identical timestamps, enabling UNIQUE constraint dedup.
    """
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


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
    # Server stores credentials (email / password / optional TOTP) but never
    # calls the carrier itself — the Chrome extension fetches them, performs
    # the login in a real browser tab, scrapes the orders page, and POSTs
    # the HTML back to ``/browser-push``. Used by carriers whose production
    # login flow rejects headless replay (Amazon CAPTCHA, DPD Cloudflare).
    BROWSER_PUSH = "browser_push"
    # Hybrid flow: server stores credentials so the Chrome extension can
    # auto-login. After login the extension harvests a bearer token from
    # the authenticated page (e.g. ``sessionStorage``) and PATCHes it to
    # ``/accounts/{id}/token``. Unlike ``BROWSER_PUSH``, the server then
    # uses that token to call the carrier's API directly — so
    # ``sync_packages()`` runs server-side. Used by PostNL (GraphQL at
    # ``jouw.postnl.nl``).
    EXTENSION_TOKEN = "extension_token"


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


class CarrierSyncError(Exception):
    """Raised when the carrier site returns an error page instead of parcels.

    Distinct from :class:`CarrierAuthError` — the session may be fine but
    the carrier is experiencing a technical issue. The service catches this
    and marks the account status as ``error`` (not ``auth_failed``) so the
    next extension sync will retry rather than prompting re-authentication.
    """

    def __init__(self, carrier: str, message: str) -> None:
        self.carrier = carrier
        self.message = message
        super().__init__(f"[{carrier}] Sync error: {message}")


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
    delivery_window_end: datetime | None = None
    events: list[TrackingEvent] = field(default_factory=list)
    # Destination postal code discovered during an authenticated sync.
    # Persisted on the package row so that once the carrier's account list
    # drops the parcel, the unified refresh loop can still call public
    # track(tn, postal_code=...) for carriers that require it (PostNL, GLS).
    postal_code: str | None = None
    # Direct public-tracking URL captured during an authenticated sync.
    # Used by carriers where (tracking_number, postal_code) alone is not
    # enough — notably Amazon, whose per-parcel share token is only visible
    # on the authenticated orders page and must be captured at discovery time
    # for unauthenticated refreshes to work afterwards.
    tracking_url: str | None = None


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

    async def validate_token(self, tokens: AuthTokens) -> None:
        """Lightweight token validation before persisting.

        Defaults to a minimal sync (lookback_days=1). Carriers where replay
        is unreliable (e.g. DPD — Cloudflare binds ``cf_clearance`` to the
        issuing browser's TLS fingerprint) should override this with a
        format-only check.
        """
        await self.sync_packages(tokens, lookback_days=1)

    def get_updated_tokens(self) -> AuthTokens | None:
        """Return updated tokens if the last sync refreshed them (e.g. browser cookies)."""
        return None

    async def refresh_tokens(self, tokens: AuthTokens) -> AuthTokens:
        """Refresh expired tokens. Returns new tokens."""
        raise NotImplementedError(f"{self.name} does not support token refresh")
