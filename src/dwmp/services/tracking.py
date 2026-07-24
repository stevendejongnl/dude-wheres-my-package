import json
import logging
import re
from dataclasses import asdict

from dwmp.carriers.base import (
    AccountStatus,
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
    CarrierSyncError,
    CarrierTransientError,
    TrackingResult,
    TrackingStatus,
)
from dwmp.storage.repository import PackageRepository

logger = logging.getLogger(__name__)

# Amazon's ship-track page renders tracking IDs with a leading label
# ("Tracking ID:", "Tracking-id:") that a DOM-scrape can fail to strip if the
# page's exact wording drifts (see the extension's service-worker.js scraper).
# Stripping it here too means a future scrape regression degrades to a
# correct dedup instead of a duplicate package row.
_TRACKING_ID_LABEL_RE = re.compile(r"^\s*Tracking[\s-]?id:?\s*", re.IGNORECASE)


def _normalize_tracking_number(tracking_number: str) -> str:
    return _TRACKING_ID_LABEL_RE.sub("", tracking_number).strip()


class TrackingService:
    def __init__(
        self,
        repository: PackageRepository,
        carriers: dict[str, CarrierBase],
    ) -> None:
        self._repository = repository
        self._carriers = carriers

    def list_carriers(self) -> list[str]:
        return sorted(self._carriers.keys())

    def get_carrier(self, name: str) -> CarrierBase | None:
        return self._carriers.get(name)

    # --- Status change detection ---

    async def _update_package_status(
        self,
        pkg_id: int,
        new_status: str,
        tracking_number: str,
        carrier: str,
        label: str | None,
        description: str | None = None,
        estimated_delivery: str | None = None,
        delivery_window_end: str | None = None,
    ) -> None:
        """Update package status and create a notification if it changed."""
        existing = await self._repository.get_package(pkg_id)
        old_status = existing["current_status"] if existing else "unknown"
        await self._repository.update_status(
            pkg_id, new_status,
            estimated_delivery=estimated_delivery,
            delivery_window_end=delivery_window_end,
        )
        if old_status != new_status:
            await self._repository.add_notification(
                package_id=pkg_id,
                old_status=old_status,
                new_status=new_status,
                tracking_number=tracking_number,
                carrier=carrier,
                label=label,
                description=description,
            )

    async def notify_auth_failure(self, carrier: str, message: str) -> None:
        """Create a notification when a carrier account fails to authenticate.

        Suppresses the notification when the most recent notification for this
        carrier is already an auth_failed — avoids spamming the user on every
        sync cycle.  A new notification *will* be created once a successful
        sync (which produces a status-change notification) clears the streak.
        """
        if await self._repository.has_recent_auth_failure(carrier):
            return
        await self._repository.add_notification(
            package_id=None,
            old_status="connected",
            new_status="auth_failed",
            tracking_number="Account",
            carrier=carrier,
            description=message,
        )

    async def notify_cloudflare_challenge(self, carrier: str) -> bool:
        """Create a notification when a sync is blocked by a Cloudflare challenge.

        Same dedup as :meth:`notify_auth_failure` — suppressed while the most
        recent notification for this carrier is already a cloudflare_challenge.
        The HA integration turns the notification into a
        ``dwmp_package_status_changed`` event for automations.
        Returns ``True`` when a notification was actually created.
        """
        if await self._repository.has_recent_auth_failure(carrier, status="cloudflare_challenge"):
            return False
        await self._repository.add_notification(
            package_id=None,
            old_status="syncing",
            new_status="cloudflare_challenge",
            tracking_number="Account",
            carrier=carrier,
            description="Sync blocked by a Cloudflare check — open the Kasm browser and solve it.",
        )
        return True

    # --- Account management ---

    async def validate_account_credentials(
        self,
        carrier_name: str,
        username: str,
        password: str,
        totp_secret: str | None = None,
    ) -> AuthTokens:
        """Prepare tokens for a credentials-based or extension-driven carrier.

        For :class:`AuthType.CREDENTIALS` carriers (DHL) this actually calls
        ``carrier.login()`` to validate the credentials live against the
        upstream.

        For :class:`AuthType.BROWSER_PUSH` (Amazon, DPD) and
        :class:`AuthType.BROWSER_PAYLOAD` (PostNL) carriers the server never
        logs in itself — the Chrome extension uses the stored credentials in
        a real browser tab. So we just package the inputs into an
        :class:`AuthTokens` for the caller to persist. The credentials live
        in ``refresh_token`` (JSON) where the extension reads them via
        ``get_account_credentials``.
        """
        carrier = self._carriers.get(carrier_name)
        if carrier is None:
            raise ValueError(f"Unknown carrier: {carrier_name}")

        if carrier.auth_type in (AuthType.BROWSER_PUSH, AuthType.BROWSER_PAYLOAD):
            creds: dict[str, str] = {"email": username, "password": password}
            if totp_secret:
                creds["totp_secret"] = totp_secret
            return AuthTokens(
                access_token="",
                refresh_token=json.dumps(creds),
            )

        if carrier.auth_type != AuthType.CREDENTIALS:
            raise ValueError(f"{carrier_name} does not use credentials")

        try:
            return await carrier.login(
                username, password, totp_secret=totp_secret or ""
            )
        except Exception as exc:
            raise CarrierAuthError(
                carrier_name,
                f"Login failed. Check your credentials or the carrier's login "
                f"flow may have changed. ({exc})",
            ) from exc

    async def connect_account_credentials(
        self,
        carrier_name: str,
        username: str,
        password: str,
        lookback_days: int = 30,
        totp_secret: str | None = None,
        postal_code: str | None = None,
    ) -> dict:
        tokens = await self.validate_account_credentials(
            carrier_name, username, password, totp_secret=totp_secret
        )
        carrier = self._carriers.get(carrier_name)
        auth_type_str = carrier.auth_type.value if carrier else "credentials"

        account_id = await self._repository.add_account(
            carrier=carrier_name,
            auth_type=auth_type_str,
            tokens=asdict(tokens),
            username=username,
            lookback_days=lookback_days,
            postal_code=postal_code,
        )
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def validate_account_manual_token(
        self,
        carrier_name: str,
        access_token: str,
        refresh_token: str | None = None,
        user_agent: str | None = None,
    ) -> AuthTokens:
        """Validate a manual token. Raises CarrierAuthError on failure.

        Delegates to the carrier's ``validate_token`` method, which defaults
        to a minimal sync but can be overridden (e.g. DPD skips Playwright
        replay because Cloudflare blocks it).
        """
        carrier = self._carriers.get(carrier_name)
        if carrier is None:
            raise ValueError(f"Unknown carrier: {carrier_name}")

        tokens = AuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            user_agent=user_agent,
        )

        try:
            await carrier.validate_token(tokens)
        except Exception as exc:
            raise CarrierAuthError(
                carrier_name,
                f"Token validation failed. The token may be expired, malformed, "
                f"or the carrier's API may have changed. ({exc})",
            ) from exc

        return tokens

    async def connect_account_manual_token(
        self,
        carrier_name: str,
        access_token: str,
        refresh_token: str | None = None,
        lookback_days: int = 30,
        user_agent: str | None = None,
        postal_code: str | None = None,
    ) -> dict:
        tokens = await self.validate_account_manual_token(
            carrier_name, access_token, refresh_token, user_agent=user_agent
        )

        account_id = await self._repository.add_account(
            carrier=carrier_name,
            auth_type="manual_token",
            tokens=asdict(tokens),
            lookback_days=lookback_days,
            postal_code=postal_code,
        )
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def update_account_credentials(
        self,
        account_id: int,
        carrier_name: str,
        username: str,
        password: str,
        lookback_days: int = 30,
        totp_secret: str | None = None,
        postal_code: str | None = None,
    ) -> dict:
        """Re-validate credentials and update an existing account in place."""
        tokens = await self.validate_account_credentials(
            carrier_name, username, password, totp_secret=totp_secret,
        )
        updated = await self._repository.update_account(
            account_id=account_id,
            tokens=asdict(tokens),
            username=username,
            lookback_days=lookback_days,
            postal_code=postal_code,
        )
        if not updated:
            raise ValueError(f"Account {account_id} not found")
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def update_account_manual_token(
        self,
        account_id: int,
        carrier_name: str,
        access_token: str,
        refresh_token: str | None = None,
        lookback_days: int = 30,
        user_agent: str | None = None,
        postal_code: str | None = None,
    ) -> dict:
        """Re-validate a manual token and update an existing account in place."""
        existing = await self._repository.get_account(account_id)
        if existing is None:
            raise ValueError(f"Account {account_id} not found")

        # Preserve the existing refresh_token when no new one is provided — it
        # holds the stored credentials JSON the extension needs to auto-login.
        if refresh_token is None:
            existing_tokens = existing.get("tokens") or {}
            refresh_token = existing_tokens.get("refresh_token")

        tokens = await self.validate_account_manual_token(
            carrier_name, access_token, refresh_token, user_agent=user_agent,
        )
        updated = await self._repository.update_account(
            account_id=account_id,
            tokens=asdict(tokens),
            username=existing.get("username"),
            lookback_days=lookback_days,
            postal_code=postal_code,
        )
        if not updated:
            raise ValueError(f"Account {account_id} not found")
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def get_account_credentials(self, account_id: int) -> dict:
        """Return stored login credentials for the Chrome extension.

        For credentials-based accounts (e.g. DPD), the email/password is
        stored as JSON in ``refresh_token`` so the extension can fill in
        the carrier's Keycloak login form in a real browser tab.
        """
        account = await self._repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        tokens = account.get("tokens") or {}
        refresh = tokens.get("refresh_token")
        if refresh and isinstance(refresh, str):
            try:
                creds = json.loads(refresh)
                if isinstance(creds, dict) and (
                    creds.get("email") or creds.get("username")
                ):
                    return {
                        "has_credentials": True,
                        "username": creds.get("email")
                        or creds.get("username", ""),
                        "password": creds.get("password", ""),
                    }
            except (json.JSONDecodeError, TypeError):
                pass

        # DHL stores credentials as "email:password" in access_token (refresh_token is None).
        if account.get("carrier") == "dhl":
            access = tokens.get("access_token", "")
            if access and ":" in access:
                email, _, password = access.partition(":")
                return {"has_credentials": True, "username": email, "password": password}

        return {"has_credentials": False}

    async def find_account_by_carrier(self, carrier_name: str) -> dict | None:
        """Find the first connected account for a carrier."""
        accounts = await self._repository.list_accounts()
        for acct in accounts:
            if acct["carrier"] == carrier_name:
                return acct
        return None

    async def validate_account_credentials_by_id(self, account_id: int) -> None:
        """Probe a stuck auth_failed account with its stored credentials.

        For CREDENTIALS carriers (e.g. DHL): re-attempts ``carrier.login()``
        with the stored email/password.  On success the account transitions
        back to CONNECTED so the next sync cycle picks it up normally.

        For browser-driven carriers (BROWSER_PUSH / BROWSER_PAYLOAD) there
        are no server-side credentials to test — skip silently so the
        scheduler doesn't waste time on DPD / PostNL accounts.
        """
        account = await self._repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        carrier = self._carriers.get(account["carrier"])
        if carrier is None:
            raise ValueError(f"Unknown carrier: {account['carrier']}")

        if carrier.auth_type in (AuthType.BROWSER_PUSH, AuthType.BROWSER_PAYLOAD):
            return

        tokens_dict = account.get("tokens") or {}
        username, password = "", ""

        # Try refresh_token first (browser-push format: JSON {"email":…,"password":…})
        refresh = tokens_dict.get("refresh_token")
        if refresh and isinstance(refresh, str):
            try:
                creds = json.loads(refresh)
                if isinstance(creds, dict):
                    username = creds.get("email") or creds.get("username", "")
                    password = creds.get("password", "")
            except (json.JSONDecodeError, TypeError):
                pass

        # CREDENTIALS carriers store "email:password" in access_token
        if not username:
            access = tokens_dict.get("access_token", "")
            if access and ":" in access:
                username, _, password = access.partition(":")

        if not username:
            return

        await carrier.login(username, password)
        await self._repository.update_account_status(account_id, AccountStatus.CONNECTED)

    async def list_accounts(self) -> list[dict]:
        return await self._repository.list_accounts()

    async def get_account(self, account_id: int) -> dict | None:
        return await self._repository.get_account(account_id)

    async def _persist_account_results(
        self,
        account_id: int,
        account: dict,
        results: list[TrackingResult],
    ) -> list[dict]:
        """Persist sync results discovered from any account-backed source."""
        await self._repository.update_account_status(
            account_id, AccountStatus.CONNECTED
        )
        await self._repository.update_account_last_synced(account_id)

        acct_postal = account.get("postal_code") or ""

        synced: list[dict] = []
        for result in results:
            pkg_postal = result.postal_code or acct_postal or None

            existing = await self._repository.find_package(
                result.tracking_number, result.carrier
            )
            if existing:
                pkg_id = existing["id"]
            else:
                pkg_id = await self._repository.add_package(
                    tracking_number=result.tracking_number,
                    carrier=result.carrier,
                    label=result.label,
                    postal_code=pkg_postal,
                    account_id=account_id,
                    source="account",
                    tracking_url=result.tracking_url,
                )
                # When we create an orderId#shipmentId package, delete the old
                # plain orderId entry (from a prior sync before multi-shipment
                # parsing was introduced) to avoid duplicate rows.
                if "#" in result.tracking_number:
                    order_id = result.tracking_number.split("#", 1)[0]
                    old = await self._repository.find_package(order_id, result.carrier)
                    if old:
                        await self._repository.delete_package(old["id"])
                        logger.debug(
                            "Deleted superseded package %s in favour of %s",
                            order_id, result.tracking_number,
                        )

            backfill_postal = (
                pkg_postal if pkg_postal and existing and not existing.get("postal_code") else None
            )
            if backfill_postal:
                await self._repository.update_package_postal_code(
                    pkg_id, backfill_postal
                )
            if result.tracking_url and existing and not existing.get("tracking_url"):
                await self._repository.update_package_tracking_url(
                    pkg_id, result.tracking_url
                )
            if result.label and existing and not existing.get("label"):
                await self._repository.update_package_label(pkg_id, result.label)

            latest_desc = result.events[-1].description if result.events else None
            est = result.estimated_delivery.isoformat() if result.estimated_delivery else None
            win_end = result.delivery_window_end.isoformat() if result.delivery_window_end else None
            stored_status = existing.get("current_status") if existing else None
            is_downgrade = (
                result.status == TrackingStatus.UNKNOWN
                and stored_status
                and stored_status != TrackingStatus.UNKNOWN.value
            )
            if is_downgrade:
                await self._repository.mark_refreshed(pkg_id)
                logger.debug(
                    "Preserved status for package %s (%s): sync returned UNKNOWN, stored=%s",
                    pkg_id, result.carrier, stored_status,
                )
            else:
                await self._update_package_status(
                    pkg_id, result.status.value, result.tracking_number,
                    result.carrier, existing.get("label") if existing else None,
                    description=latest_desc,
                    estimated_delivery=est,
                    delivery_window_end=win_end,
                )
            for event in result.events:
                await self._repository.add_event(
                    package_id=pkg_id,
                    timestamp=event.timestamp,
                    status=event.status.value,
                    description=event.description,
                    location=event.location,
                )

            pkg = await self.get_package(pkg_id)
            if pkg:
                synced.append(pkg)

        return synced

    async def delete_account(self, account_id: int) -> bool:
        return await self._repository.delete_account(account_id)

    async def update_account_settings(
        self,
        account_id: int,
        lookback_days: int,
        postal_code: str | None = None,
    ) -> None:
        """Update only non-credential settings (lookback_days, postal_code)."""
        updated = await self._repository.update_account_settings(
            account_id, lookback_days, postal_code=postal_code,
        )
        if not updated:
            raise ValueError(f"Account {account_id} not found")

    async def save_account_credentials(
        self,
        account_id: int,
        carrier_name: str,
        username: str,
        password: str,
        lookback_days: int = 30,
        totp_secret: str | None = None,
        postal_code: str | None = None,
    ) -> None:
        """Save updated credentials WITHOUT re-validating.

        Used by the Edit flow — the user already tested via the Test button,
        so re-running Playwright (which may rate-limit or hit a captcha) is
        wasteful.  The next sync will use these credentials and surface any
        auth issue via the normal notification path.
        """
        # Store credentials in the same shape as the carrier's login() does
        # so _relogin() can pick them up: refresh_token holds the JSON creds.
        creds_json = json.dumps({"email": username, "password": password,
                                 "totp_secret": totp_secret})
        # Preserve the existing access_token (cookies) — they may still be
        # valid; if not, _relogin() uses the credentials we just stored.
        existing = await self._repository.get_account(account_id)
        if existing is None:
            raise ValueError(f"Account {account_id} not found")
        existing_tokens = existing.get("tokens") or {}
        tokens_dict = {
            "access_token": existing_tokens.get("access_token", ""),
            "refresh_token": creds_json,
            "user_agent": existing_tokens.get("user_agent"),
        }
        updated = await self._repository.update_account(
            account_id=account_id,
            tokens=tokens_dict,
            username=username,
            lookback_days=lookback_days,
            postal_code=postal_code,
        )
        if not updated:
            raise ValueError(f"Account {account_id} not found")

    async def set_account_sync_enabled(
        self, account_id: int, enabled: bool,
    ) -> bool:
        return await self._repository.update_account_sync_enabled(
            account_id, enabled,
        )

    async def sync_account(self, account_id: int) -> list[dict]:
        account = await self._repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        carrier = self._carriers.get(account["carrier"])
        if carrier is None:
            raise ValueError(f"Unknown carrier: {account['carrier']}")

        # Browser-push and browser-payload carriers (Amazon, DPD, PostNL) are
        # driven entirely by the Chrome extension — the extension scrapes the
        # carrier site in a real browser tab and POSTs the result to the
        # server, so the scheduler and the account-row "Sync" button never
        # talk to the upstream directly.
        if carrier.auth_type in (AuthType.BROWSER_PUSH, AuthType.BROWSER_PAYLOAD):
            logger.info(
                "Skipping server-side sync for %s account %d — "
                "browser-driven carriers sync via the Chrome extension.",
                account["carrier"], account_id,
            )
            await self._repository.update_account_status(
                account_id, AccountStatus.CONNECTED
            )
            return []

        tokens_dict = account.get("tokens") or {}
        tokens = AuthTokens(
            access_token=tokens_dict.get("access_token", ""),
            refresh_token=tokens_dict.get("refresh_token"),
            expires_at=None,
            user_agent=tokens_dict.get("user_agent"),
        )

        try:
            results = await carrier.sync_packages(
                tokens, lookback_days=account["lookback_days"]
            )
        except CarrierAuthError:
            message = (
                f"Sync failed for {account['carrier']}. "
                f"The carrier's API or login flow may have changed. "
                f"Try re-authenticating your account."
            )
            logger.warning(message)
            await self._repository.update_account_status(
                account_id, AccountStatus.AUTH_FAILED, message
            )
            raise
        except CarrierTransientError as exc:
            logger.info(
                "Transient sync failure for account %d (%s): %s",
                account_id, account["carrier"], exc,
            )
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected sync error for account %d (%s)",
                account_id, account["carrier"],
            )
            raise CarrierTransientError(account["carrier"], str(exc)) from exc

        # Persist refreshed tokens (e.g. updated browser cookies)
        updated_tokens = carrier.get_updated_tokens()
        if updated_tokens:
            await self._repository.update_account_tokens(
                account_id, asdict(updated_tokens)
            )
        return await self._persist_account_results(account_id, account, results)

    async def sync_account_from_html(
        self, account_id: int, html: str
    ) -> list[dict]:
        """Sync an account using raw HTML captured by the user's browser.

        Bypasses Playwright entirely — the user's browser has the valid
        session and captures the page HTML via a bookmarklet. We just
        parse it the same way sync_packages would.
        """
        account = await self._repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        carrier = self._carriers.get(account["carrier"])
        if carrier is None:
            raise ValueError(f"Unknown carrier: {account['carrier']}")

        if not hasattr(carrier, "_parse_parcels_page"):
            raise ValueError(
                f"{account['carrier']} does not support browser-push sync"
            )

        try:
            results = carrier._parse_parcels_page(html, account["lookback_days"])
        except CarrierAuthError:
            await self._repository.update_account_status(
                account_id, AccountStatus.AUTH_FAILED
            )
            raise
        except CarrierSyncError:
            await self._repository.update_account_status(
                account_id, AccountStatus.ERROR
            )
            raise

        return await self._persist_account_results(account_id, account, results)

    async def sync_account_from_browser_payload(
        self, account_id: int, payload: dict
    ) -> list[dict]:
        """Sync an account using structured data harvested in the browser."""
        account = await self._repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        carrier = self._carriers.get(account["carrier"])
        if carrier is None:
            raise ValueError(f"Unknown carrier: {account['carrier']}")

        if not hasattr(carrier, "_parse_browser_payload"):
            raise ValueError(
                f"{account['carrier']} does not support browser payload sync"
            )

        try:
            results = carrier._parse_browser_payload(payload, account["lookback_days"])
        except CarrierAuthError:
            await self._repository.update_account_status(
                account_id, AccountStatus.AUTH_FAILED
            )
            raise
        except CarrierSyncError:
            await self._repository.update_account_status(
                account_id, AccountStatus.ERROR
            )
            raise

        return await self._persist_account_results(account_id, account, results)

    # --- Package management ---

    async def add_package(
        self,
        tracking_number: str,
        carrier: str,
        label: str | None = None,
        postal_code: str | None = None,
    ) -> dict:
        tracking_number = _normalize_tracking_number(tracking_number)
        pkg_id = await self._repository.add_package(
            tracking_number=tracking_number,
            carrier=carrier,
            label=label,
            postal_code=postal_code,
            source="manual",
        )
        pkg = await self._repository.get_package(pkg_id)
        assert pkg is not None
        return pkg

    async def list_packages(self) -> list[dict]:
        return await self._repository.list_packages()

    async def get_package(self, package_id: int) -> dict | None:
        pkg = await self._repository.get_package(package_id)
        if pkg is None:
            return None
        events = await self._repository.get_events(package_id)
        return {**pkg, "events": events}

    async def delete_package(self, package_id: int) -> bool:
        return await self._repository.delete_package(package_id)

    async def refresh_package(self, package_id: int) -> dict | None:
        pkg = await self._repository.get_package(package_id)
        if pkg is None:
            return None

        carrier = self._carriers.get(pkg["carrier"])
        if carrier is None:
            events = await self._repository.get_events(package_id)
            return {**pkg, "events": events}

        try:
            result = await carrier.track(
                pkg["tracking_number"],
                postal_code=pkg.get("postal_code") or "",
                tracking_url=pkg.get("tracking_url") or "",
            )
        except CarrierTransientError as exc:
            logger.info(
                "Transient error refreshing package %s (%s): %s — skipping this cycle",
                package_id, pkg["carrier"], exc,
            )
            await self._repository.mark_refreshed(package_id, failure=True)
            return await self.get_package(package_id)

        # Downgrade safeguard: never overwrite a known status with UNKNOWN —
        # whether the carrier couldn't resolve the package (empty result) or
        # returned events whose phrases we don't map yet. Matches the
        # account-sync guard in _persist_account_results. Events (if any) are
        # still appended below; an empty UNKNOWN counts as a failure so the
        # scheduler can eventually stop polling dead packages.
        stored_status = pkg.get("current_status", TrackingStatus.UNKNOWN.value)
        preserve = (
            result.status == TrackingStatus.UNKNOWN
            and stored_status != TrackingStatus.UNKNOWN.value
        )
        if preserve:
            await self._repository.mark_refreshed(
                package_id, failure=not result.events
            )
            logger.debug(
                "Preserved status for package %s (%s): track() returned UNKNOWN, stored=%s",
                package_id, pkg["carrier"], stored_status,
            )
        else:
            latest_desc = result.events[-1].description if result.events else None
            est = result.estimated_delivery.isoformat() if result.estimated_delivery else None
            win_end = result.delivery_window_end.isoformat() if result.delivery_window_end else None
            await self._update_package_status(
                package_id, result.status.value,
                pkg["tracking_number"], pkg["carrier"], pkg.get("label"),
                description=latest_desc,
                estimated_delivery=est,
                delivery_window_end=win_end,
            )
        for event in result.events:
            await self._repository.add_event(
                package_id=package_id,
                timestamp=event.timestamp,
                status=event.status.value,
                description=event.description,
                location=event.location,
            )

        return await self.get_package(package_id)

    # --- Notification management ---

    async def get_unread_notification_count(self) -> int:
        return await self._repository.get_unread_count()

    async def list_notifications(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        return await self._repository.list_notifications(limit, offset)

    async def mark_notification_read(self, notification_id: int) -> bool:
        return await self._repository.mark_notification_read(notification_id)

    async def mark_all_notifications_read(self) -> int:
        return await self._repository.mark_all_read()

    async def delete_old_notifications(self, days: int = 30) -> int:
        return await self._repository.delete_old_notifications(days)
