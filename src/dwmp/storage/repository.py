import json
import os

import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    package_id INTEGER NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
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

    async def close(self) -> None:
        if self._db:
            await self._db.close()

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
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        tokens_json = json.dumps(tokens) if tokens else None
        try:
            cursor = await self.db.execute(
                """INSERT INTO accounts (carrier, auth_type, tokens, username, lookback_days, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (carrier, auth_type, tokens_json, username, lookback_days, now, now),
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

    async def update_account_tokens(
        self, account_id: int, tokens: dict
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE accounts SET tokens = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tokens), now, account_id),
        )
        await self.db.commit()

    async def update_account_status(
        self, account_id: int, status: str, message: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE accounts SET status = ?, status_message = ?, updated_at = ? WHERE id = ?",
            (status, message, now, account_id),
        )
        await self.db.commit()

    async def update_account_last_synced(self, account_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE accounts SET last_synced = ?, updated_at = ? WHERE id = ?",
            (now, now, account_id),
        )
        await self.db.commit()

    # --- Package methods ---

    async def add_package(
        self,
        tracking_number: str,
        carrier: str,
        label: str | None = None,
        postal_code: str | None = None,
        account_id: int | None = None,
        source: str = "manual",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        try:
            cursor = await self.db.execute(
                """INSERT INTO packages
                   (tracking_number, carrier, label, postal_code, account_id, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (tracking_number, carrier, label, postal_code, account_id, source, now, now),
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

    async def update_status(self, package_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE packages SET current_status = ?, updated_at = ? WHERE id = ?",
            (status, now, package_id),
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

    async def add_notification(
        self,
        package_id: int,
        old_status: str,
        new_status: str,
        tracking_number: str,
        carrier: str,
        label: str | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self.db.execute(
            """INSERT INTO notifications
               (package_id, old_status, new_status, tracking_number, carrier, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (package_id, old_status, new_status, tracking_number, carrier, label, now),
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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM notifications WHERE created_at < ?", (cutoff,)
        )
        await self.db.commit()
        return cursor.rowcount
