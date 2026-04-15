"""Populate a DWMP SQLite DB with the canonical sample data.

Example:
    uv run python -m dwmp.testing --db /tmp/dwmp-demo.db

    DB_PATH=/tmp/dwmp-demo.db POLL_INTERVAL_MINUTES=99999 \\
        uv run uvicorn dwmp.api.app:app --port 8087

If ``--db`` is omitted, falls back to ``$DB_PATH`` or ``dwmp-demo.db`` in
the current directory. Any pre-existing file at the target path is removed
first so the seed is deterministic — handy for screenshots and manual QA.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dwmp.storage.repository import PackageRepository

from . import seed_all


async def _run(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    repo = PackageRepository(db_path=db_path)
    await repo.init()
    try:
        result = await seed_all(repo)
    finally:
        await repo.close()
    print(f"Seeded {db_path}")
    print(f"  accounts:      {len(result['accounts'])}")
    print(f"  packages:      {len(result['packages'])}")
    print(f"  notifications: {len(result['notifications'])}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DB_PATH", "dwmp-demo.db"),
        help="Path to the SQLite file to create (overwrites if exists).",
    )
    args = parser.parse_args()
    asyncio.run(_run(Path(args.db)))


if __name__ == "__main__":
    main()
