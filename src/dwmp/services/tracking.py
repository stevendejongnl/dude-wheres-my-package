import logging
from dataclasses import asdict

from dwmp.carriers.base import (
    AccountStatus,
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
    TrackingStatus,
)
from dwmp.storage.repository import PackageRepository

logger = logging.getLogger(__name__)


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
    ) -> None:
        """Update package status and create a notification if it changed."""
        existing = await self._repository.get_package(pkg_id)
        old_status = existing["current_status"] if existing else "unknown"
        await self._repository.update_status(
            pkg_id, new_status, estimated_delivery=estimated_delivery,
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
        """Create a notification when a carrier account fails to authenticate."""
        await self._repository.add_notification(
            package_id=None,
            old_status="connected",
            new_status="auth_failed",
            tracking_number="Account",
            carrier=carrier,
            label=message,
        )

    # --- Account management ---

    async def connect_account_oauth(
        self, carrier_name: str, callback_url: str
    ) -> dict:
        carrier = self._carriers.get(carrier_name)
        if carrier is None:
            raise ValueError(f"Unknown carrier: {carrier_name}")
        if carrier.auth_type != AuthType.OAUTH:
            raise ValueError(f"{carrier_name} does not use OAuth")

        auth_url = await carrier.get_auth_url(callback_url)
        return {"auth_url": auth_url, "carrier": carrier_name}

    async def handle_oauth_callback(
        self,
        carrier_name: str,
        code: str,
        callback_url: str,
        lookback_days: int = 30,
    ) -> dict:
        carrier = self._carriers.get(carrier_name)
        if carrier is None:
            raise ValueError(f"Unknown carrier: {carrier_name}")

        try:
            tokens = await carrier.handle_callback(code, callback_url)
        except Exception as exc:
            raise CarrierAuthError(
                carrier_name,
                f"OAuth callback failed. The carrier's login flow may have changed. "
                f"Try re-authenticating or check for API updates. ({exc})",
            ) from exc

        account_id = await self._repository.add_account(
            carrier=carrier_name,
            auth_type="oauth",
            tokens=asdict(tokens),
            lookback_days=lookback_days,
        )
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def validate_account_credentials(
        self,
        carrier_name: str,
        username: str,
        password: str,
        totp_secret: str | None = None,
    ) -> AuthTokens:
        """Attempt a credentials login without persisting. Raises CarrierAuthError on failure."""
        carrier = self._carriers.get(carrier_name)
        if carrier is None:
            raise ValueError(f"Unknown carrier: {carrier_name}")
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

        account_id = await self._repository.add_account(
            carrier=carrier_name,
            auth_type="credentials",
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
        tokens = await self.validate_account_manual_token(
            carrier_name, access_token, refresh_token, user_agent=user_agent,
        )
        updated = await self._repository.update_account(
            account_id=account_id,
            tokens=asdict(tokens),
            username=None,
            lookback_days=lookback_days,
            postal_code=postal_code,
        )
        if not updated:
            raise ValueError(f"Account {account_id} not found")
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def list_accounts(self) -> list[dict]:
        return await self._repository.list_accounts()

    async def get_account(self, account_id: int) -> dict | None:
        return await self._repository.get_account(account_id)

    async def delete_account(self, account_id: int) -> bool:
        return await self._repository.delete_account(account_id)

    async def sync_account(self, account_id: int) -> list[dict]:
        account = await self._repository.get_account(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        carrier = self._carriers.get(account["carrier"])
        if carrier is None:
            raise ValueError(f"Unknown carrier: {account['carrier']}")

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
        except Exception as exc:
            message = (
                f"Sync failed for {account['carrier']}. "
                f"The carrier's API or login flow may have changed. "
                f"Try re-authenticating your account. ({exc})"
            )
            logger.warning(message)
            await self._repository.update_account_status(
                account_id, AccountStatus.AUTH_FAILED, message
            )
            raise CarrierAuthError(account["carrier"], message) from exc

        # Mark account as healthy
        await self._repository.update_account_status(
            account_id, AccountStatus.CONNECTED
        )
        await self._repository.update_account_last_synced(account_id)

        # Persist refreshed tokens (e.g. updated browser cookies)
        updated_tokens = carrier.get_updated_tokens()
        if updated_tokens:
            await self._repository.update_account_tokens(
                account_id, asdict(updated_tokens)
            )

        # The account's postal_code is the delivery address — apply it to
        # every discovered package so public tracking (DPD verification,
        # GLS lookup) works even after account cookies expire.
        acct_postal = account.get("postal_code") or ""

        synced: list[dict] = []
        for result in results:
            # Prefer carrier-provided postal code, fall back to account's
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
                    postal_code=pkg_postal,
                    account_id=account_id,
                    source="account",
                    tracking_url=result.tracking_url,
                )

            # Backfill postal_code / tracking_url onto pre-existing rows when
            # discovery newly surfaces them or the account now has a postal_code
            # that wasn't set before. Without this, packages added before the
            # carrier started capturing these fields would never benefit from
            # public-track fallback once they drop off the account list.
            backfill_postal = pkg_postal if pkg_postal and existing and not existing.get("postal_code") else None
            if backfill_postal:
                await self._repository.update_package_postal_code(
                    pkg_id, backfill_postal
                )
            if result.tracking_url and existing and not existing.get("tracking_url"):
                await self._repository.update_package_tracking_url(
                    pkg_id, result.tracking_url
                )

            # Latest event description for the notification
            latest_desc = result.events[-1].description if result.events else None
            est = result.estimated_delivery.isoformat() if result.estimated_delivery else None
            await self._update_package_status(
                pkg_id, result.status.value, result.tracking_number,
                result.carrier, existing.get("label") if existing else None,
                description=latest_desc,
                estimated_delivery=est,
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

        results = carrier._parse_parcels_page(html, account["lookback_days"])

        # Mark account healthy
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
                    postal_code=pkg_postal,
                    account_id=account_id,
                    source="account",
                    tracking_url=result.tracking_url,
                )

            if pkg_postal and existing and not existing.get("postal_code"):
                await self._repository.update_package_postal_code(
                    pkg_id, pkg_postal
                )

            latest_desc = result.events[-1].description if result.events else None
            est = result.estimated_delivery.isoformat() if result.estimated_delivery else None
            await self._update_package_status(
                pkg_id, result.status.value, result.tracking_number,
                result.carrier, existing.get("label") if existing else None,
                description=latest_desc,
                estimated_delivery=est,
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

    # --- Package management ---

    async def add_package(
        self,
        tracking_number: str,
        carrier: str,
        label: str | None = None,
        postal_code: str | None = None,
    ) -> dict:
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

        result = await carrier.track(
            pkg["tracking_number"],
            postal_code=pkg.get("postal_code") or "",
            tracking_url=pkg.get("tracking_url") or "",
        )

        # Downgrade safeguard: a public track() that returns UNKNOWN with no
        # events means the carrier couldn't resolve the package (missing
        # postal_code, unimplemented endpoint, transient error). Don't let that
        # overwrite a good status from a prior sync — just bump last_refreshed_at
        # so the scheduler won't retry it immediately.
        is_empty_result = (
            result.status == TrackingStatus.UNKNOWN
            and not result.events
        )
        stored_status = pkg.get("current_status", TrackingStatus.UNKNOWN.value)
        if is_empty_result and stored_status != TrackingStatus.UNKNOWN.value:
            await self._repository.mark_refreshed(package_id)
            logger.debug(
                "Preserved status for package %s (%s): track() returned empty UNKNOWN",
                package_id, pkg["carrier"],
            )
            return await self.get_package(package_id)

        latest_desc = result.events[-1].description if result.events else None
        est = result.estimated_delivery.isoformat() if result.estimated_delivery else None
        await self._update_package_status(
            package_id, result.status.value,
            pkg["tracking_number"], pkg["carrier"], pkg.get("label"),
            description=latest_desc,
            estimated_delivery=est,
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
