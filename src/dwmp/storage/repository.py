import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

DB_PATH = Path(os.environ.get("DB_PATH", "dwmp.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    carrier TEXT NOT NULL,
    auth_type TEXT NOT NULL,
    tokens TEXT,
    username TEXT,
    lookback_days INTEGER NOT NULL DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'connected',
    status_message TEXT,
    last_synced TEXT,
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(carrier, username)
);

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_number TEXT NOT NULL,
    carrier TEXT NOT NULL,
    label TEXT,
    postal_code TEXT,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    current_status TEXT NOT NULL DEFAULT 'unknown',
    estimated_delivery TEXT,
    last_refreshed_at TEXT,
    tracking_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tracking_number, carrier)
);

CREATE TABLE IF NOT EXISTS tracking_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    location TEXT,
    UNIQUE(package_id, timestamp, status)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER REFERENCES packages(id) ON DELETE CASCADE,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    carrier TEXT NOT NULL,
    label TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


class PackageRepository:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.executescript(SCHEMA)
        await self._migrate()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def _migrate(self) -> None:
        """Run schema migrations for existing databases."""
        # v1.8: make notifications.package_id nullable for auth-failure notifications
        cursor = await self.db.execute("PRAGMA table_info(notifications)")
        for col in await cursor.fetchall():
            if col["name"] == "package_id" and col["notnull"]:
                await self.db.executescript("""
                    CREATE TABLE notifications_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        package_id INTEGER REFERENCES packages(id) ON DELETE CASCADE,
                        old_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        tracking_number TEXT NOT NULL,
                        carrier TEXT NOT NULL,
                        label TEXT,
                        is_read INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO notifications_new SELECT * FROM notifications;
                    DROP TABLE notifications;
                    ALTER TABLE notifications_new RENAME TO notifications;
                """)
                break

        # v1.20: add packages.last_refreshed_at for unified-refresh scheduling,
        # plus packages.tracking_url as the escape hatch for carriers whose
        # public tracking lookup needs more than (tracking_number, postal_code)
        # — notably Amazon, where the per-parcel share token only surfaces on
        # the authenticated orders page and must be captured at discovery time.
        cursor = await self.db.execute("PRAGMA table_info(packages)")
        cols = {col["name"] for col in await cursor.fetchall()}
        if "last_refreshed_at" not in cols:
            await self.db.execute(
                "ALTER TABLE packages ADD COLUMN last_refreshed_at TEXT"
            )
            await self.db.commit()
        if "tracking_url" not in cols:
            await self.db.execute(
                "ALTER TABLE packages ADD COLUMN tracking_url TEXT"
            )
            await self.db.commit()

        # v1.25: add accounts.postal_code so account-discovered packages
        # can inherit the delivery postal code for public tracking (DPD
        # guest verification, etc).
        cursor = await self.db.execute("PRAGMA table_info(accounts)")
        acct_cols = {col["name"] for col in await cursor.fetchall()}
        if "postal_code" not in acct_cols:
            await self.db.execute(
                "ALTER TABLE accounts ADD COLUMN postal_code TEXT"
            )
            await self.db.commit()

        # v1.38: add accounts.sync_enabled toggle
        if "sync_enabled" not in acct_cols:
            await self.db.execute(
                "ALTER TABLE accounts ADD COLUMN sync_enabled INTEGER NOT NULL DEFAULT 1"
            )
            await self.db.commit()

        # v1.27: add notifications.description for richer notification messages
        cursor = await self.db.execute("PRAGMA table_info(notifications)")
        notif_cols = {col["name"] for col in await cursor.fetchall()}
        if "description" not in notif_cols:
            await self.db.execute(
                "ALTER TABLE notifications ADD COLUMN description TEXT"
            )
            await self.db.commit()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Repository not initialized — call init() first"
        return self._db

    # --- Account methods ---

    async def add_account(
        self,
        carrier: str,
        auth_type: str,
        tokens: dict | None = None,
        username: str | None = None,
        lookback_days: int = 30,
        postal_code: str | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        tokens_json = json.dumps(tokens) if tokens else None
        try:
            cursor = await self.db.execute(
                """INSERT INTO accounts
                   (carrier, auth_type, tokens, username, lookback_days,
                    postal_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (carrier, auth_type, tokens_json, username, lookback_days,
                 postal_code, now, now),
            )
            await self.db.commit()
            assert cursor.lastrowid is not None
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            raise ValueError(f"Account for {carrier} ({username}) already exists")

    async def get_account(self, account_id: int) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tokens"] = json.loads(result["tokens"]) if result["tokens"] else None
        return result

    async def list_accounts(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM accounts ORDER BY created_at DESC"
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        for row in rows:
            row["tokens"] = json.loads(row["tokens"]) if row["tokens"] else None
        return rows

    async def delete_account(self, account_id: int) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM accounts WHERE id = ?", (account_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def update_account(
        self,
        account_id: int,
        tokens: dict,
        username: str | None = None,
        lookback_days: int = 30,
        postal_code: str | None = None,
    ) -> bool:
        """Update an existing account's credentials and settings.

        Overwrites tokens, username, lookback_days, and postal_code; resets
        status to 'connected' and clears status_message (callers reach this
        method only after a successful Test). Leaves carrier, auth_type,
        created_at, and last_synced untouched.
        """
        now = datetime.now(UTC).isoformat()
        tokens_json = json.dumps(tokens)
        try:
            cursor = await self.db.execute(
                """UPDATE accounts
                   SET tokens = ?, username = ?, lookback_days = ?,
                       postal_code = ?,
                       status = 'connected', status_message = NULL,
                       updated_at = ?
                   WHERE id = ?""",
                (tokens_json, username, lookback_days, postal_code, now,
                 account_id),
            )
            await self.db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError(
                "Another account with that username already exists for this carrier",
            )
        return cursor.rowcount > 0

    async def update_account_tokens(
        self, account_id: int, tokens: dict
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE accounts SET tokens = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tokens), now, account_id),
        )
        await self.db.commit()

    async def update_account_status(
        self, account_id: int, status: str, message: str | None = None
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE accounts SET status = ?, status_message = ?, updated_at = ? WHERE id = ?",
            (status, message, now, account_id),
        )
        await self.db.commit()

    async def update_account_last_synced(self, account_id: int) -> None:
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE accounts SET last_synced = ?, updated_at = ? WHERE id = ?",
            (now, now, account_id),
        )
        await self.db.commit()

    async def update_account_settings(
        self,
        account_id: int,
        lookback_days: int,
        postal_code: str | None = None,
    ) -> bool:
        """Update only non-credential settings without touching tokens."""
        now = datetime.now(UTC).isoformat()
        cursor = await self.db.execute(
            """UPDATE accounts
               SET lookback_days = ?, postal_code = ?, updated_at = ?
               WHERE id = ?""",
            (lookback_days, postal_code, now, account_id),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def update_account_sync_enabled(
        self, account_id: int, enabled: bool,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = await self.db.execute(
            "UPDATE accounts SET sync_enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, now, account_id),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # --- Package methods ---

    async def add_package(
        self,
        tracking_number: str,
        carrier: str,
        label: str | None = None,
        postal_code: str | None = None,
        account_id: int | None = None,
        source: str = "manual",
        tracking_url: str | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        try:
            cursor = await self.db.execute(
                """INSERT INTO packages
                   (tracking_number, carrier, label, postal_code,
                    account_id, source, tracking_url, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tracking_number, carrier, label, postal_code,
                 account_id, source, tracking_url, now, now),
            )
            await self.db.commit()
            assert cursor.lastrowid is not None
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            raise ValueError(f"Package {tracking_number} ({carrier}) is already tracked")

    async def get_package(self, package_id: int) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM packages WHERE id = ?", (package_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def find_package(self, tracking_number: str, carrier: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM packages WHERE tracking_number = ? AND carrier = ?",
            (tracking_number, carrier),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_packages(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM packages ORDER BY created_at DESC"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def list_packages_by_account(self, account_id: int) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM packages WHERE account_id = ? ORDER BY created_at DESC",
            (account_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def delete_package(self, package_id: int) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM packages WHERE id = ?", (package_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def update_status(
        self,
        package_id: int,
        status: str,
        estimated_delivery: str | None = None,
    ) -> None:
        """Update current_status, estimated_delivery, updated_at, and last_refreshed_at.

        Writing last_refreshed_at here (rather than exposing a separate method)
        means every successful status read — whether via sync_packages or
        track() — is recorded as a refresh. The scheduler uses this to avoid
        calling track() on packages an account sync just wrote.
        """
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE packages SET current_status = ?, estimated_delivery = ?,"
            " updated_at = ?, last_refreshed_at = ? WHERE id = ?",
            (status, estimated_delivery, now, now, package_id),
        )
        await self.db.commit()

    async def update_package_tracking_url(
        self, package_id: int, tracking_url: str
    ) -> None:
        """Backfill tracking_url on a package row (discovery started capturing one)."""
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE packages SET tracking_url = ?, updated_at = ? WHERE id = ?",
            (tracking_url, now, package_id),
        )
        await self.db.commit()

    async def update_package_postal_code(
        self, package_id: int, postal_code: str
    ) -> None:
        """Backfill postal_code on a package row.

        Used during account sync when the carrier now surfaces a postal_code
        it didn't before (e.g. a PostNL parcel whose detailsUrl we only
        started mining after v1.20). No-ops if the value is unchanged.
        """
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE packages SET postal_code = ?, updated_at = ? WHERE id = ?",
            (postal_code, now, package_id),
        )
        await self.db.commit()

    async def mark_refreshed(self, package_id: int) -> None:
        """Bump last_refreshed_at without changing status.

        Used when a carrier returned a result but status didn't warrant an
        update (e.g. track() returned UNKNOWN and we chose to preserve the
        existing status). Still counts as 'refreshed this cycle' so the
        scheduler won't retry it immediately.
        """
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            "UPDATE packages SET last_refreshed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, package_id),
        )
        await self.db.commit()

    # --- Event methods ---

    async def add_event(
        self,
        package_id: int,
        timestamp: datetime,
        status: str,
        description: str,
        location: str | None = None,
    ) -> None:
        await self.db.execute(
            """INSERT OR IGNORE INTO tracking_events (package_id, timestamp, status, description, location)
               VALUES (?, ?, ?, ?, ?)""",
            (package_id, timestamp.isoformat(), status, description, location),
        )
        await self.db.commit()

    async def get_events(self, package_id: int) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM tracking_events WHERE package_id = ? ORDER BY timestamp",
            (package_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    # --- Notification methods ---

    async def has_recent_auth_failure(self, carrier: str) -> bool:
        """Check if the most recent notification for *carrier* is already
        an ``auth_failed`` transition.  Returns ``True`` when a duplicate
        auth-failure notification should be suppressed."""
        cursor = await self.db.execute(
            """SELECT new_status FROM notifications
               WHERE carrier = ?
               ORDER BY created_at DESC LIMIT 1""",
            (carrier,),
        )
        row = await cursor.fetchone()
        return row is not None and row[0] == "auth_failed"

    async def add_notification(
        self,
        package_id: int | None,
        old_status: str,
        new_status: str,
        tracking_number: str,
        carrier: str,
        label: str | None = None,
        description: str | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = await self.db.execute(
            """INSERT INTO notifications
               (package_id, old_status, new_status, tracking_number, carrier, label, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (package_id, old_status, new_status, tracking_number, carrier, label, description, now),
        )
        await self.db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def get_unread_count(self) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read = 0"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def list_notifications(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def mark_notification_read(self, notification_id: int) -> bool:
        cursor = await self.db.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND is_read = 0",
            (notification_id,),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def mark_all_read(self) -> int:
        cursor = await self.db.execute(
            "UPDATE notifications SET is_read = 1 WHERE is_read = 0"
        )
        await self.db.commit()
        return cursor.rowcount

    async def delete_old_notifications(self, days: int = 30) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM notifications WHERE created_at < ?", (cutoff,)
        )
        await self.db.commit()
        return cursor.rowcount
