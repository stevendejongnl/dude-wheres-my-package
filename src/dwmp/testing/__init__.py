"""Canonical fixture data and seeders for tests and demo-DB bootstrapping.

Import from here to get a stable set of accounts / packages / notifications
that covers every carrier and notification variant the app renders. Useful
for integration tests, Playwright/visual regressions, and local manual QA.

    from dwmp.testing import seed_all
    await seed_all(repo)

See ``python -m dwmp.testing --help`` for the demo-DB CLI.
"""

from .fixtures import (
    SAMPLE_ACCOUNTS,
    SAMPLE_NOTIFICATIONS,
    SAMPLE_PACKAGES,
)
from .seeders import (
    seed_accounts,
    seed_all,
    seed_notifications,
    seed_packages,
)

__all__ = [
    "SAMPLE_ACCOUNTS",
    "SAMPLE_NOTIFICATIONS",
    "SAMPLE_PACKAGES",
    "seed_accounts",
    "seed_all",
    "seed_notifications",
    "seed_packages",
]
