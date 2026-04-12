import logging
from dataclasses import asdict

from dwmp.carriers.base import (
    AccountStatus,
    AuthTokens,
    AuthType,
    CarrierAuthError,
    CarrierBase,
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
    ) -> None:
        """Update package status and create a notification if it changed."""
        existing = await self._repository.get_package(pkg_id)
        old_status = existing["current_status"] if existing else "unknown"
        await self._repository.update_status(pkg_id, new_status)
        if old_status != new_status:
            await self._repository.add_notification(
                package_id=pkg_id,
                old_status=old_status,
                new_status=new_status,
                tracking_number=tracking_number,
                carrier=carrier,
                label=label,
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

    async def connect_account_credentials(
        self,
        carrier_name: str,
        username: str,
        password: str,
        lookback_days: int = 30,
        totp_secret: str | None = None,
    ) -> dict:
        carrier = self._carriers.get(carrier_name)
        if carrier is None:
            raise ValueError(f"Unknown carrier: {carrier_name}")
        if carrier.auth_type != AuthType.CREDENTIALS:
            raise ValueError(f"{carrier_name} does not use credentials")

        try:
            tokens = await carrier.login(
                username, password, totp_secret=totp_secret or ""
            )
        except Exception as exc:
            raise CarrierAuthError(
                carrier_name,
                f"Login failed. Check your credentials or the carrier's login "
                f"flow may have changed. ({exc})",
            ) from exc

        account_id = await self._repository.add_account(
            carrier=carrier_name,
            auth_type="credentials",
            tokens=asdict(tokens),
            username=username,
            lookback_days=lookback_days,
        )
        account = await self._repository.get_account(account_id)
        assert account is not None
        return account

    async def connect_account_manual_token(
        self,
        carrier_name: str,
        access_token: str,
        refresh_token: str | None = None,
        lookback_days: int = 30,
    ) -> dict:
        if carrier_name not in self._carriers:
            raise ValueError(f"Unknown carrier: {carrier_name}")

        tokens = AuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
        )

        account_id = await self._repository.add_account(
            carrier=carrier_name,
            auth_type="manual_token",
            tokens=asdict(tokens),
            lookback_days=lookback_days,
        )
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

        synced: list[dict] = []
        for result in results:
            existing = await self._repository.find_package(
                result.tracking_number, result.carrier
            )
            if existing:
                pkg_id = existing["id"]
            else:
                pkg_id = await self._repository.add_package(
                    tracking_number=result.tracking_number,
                    carrier=result.carrier,
                    account_id=account_id,
                    source="account",
                )

            await self._update_package_status(
                pkg_id, result.status.value, result.tracking_number,
                result.carrier, existing.get("label") if existing else None,
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
            postal_code=pkg.get("postal_code", ""),
        )

        await self._update_package_status(
            package_id, result.status.value,
            pkg["tracking_number"], pkg["carrier"], pkg.get("label"),
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
