"""Async helpers that write :mod:`dwmp.testing.fixtures` data into a repo.

Every helper takes a ``PackageRepository`` that has been ``init()``-ed by
the caller (tests typically manage this via an autouse pytest fixture).
Default payloads come from :mod:`dwmp.testing.fixtures`; pass your own list
of dicts to seed a tailored subset.
"""

from __future__ import annotations

from dwmp.storage.repository import PackageRepository

from . import fixtures


async def seed_accounts(
    repo: PackageRepository,
    records: list[dict] | None = None,
) -> dict[str, int]:
    """Insert every account in ``records``.

    Returns a ``{carrier: account_id}`` map so callers can wire notifications
    or per-account fixtures to the created rows.
    """
    records = records if records is not None else fixtures.SAMPLE_ACCOUNTS
    ids: dict[str, int] = {}
    for data in records:
        ids[data["carrier"]] = await repo.add_account(**data)
    return ids


async def seed_packages(
    repo: PackageRepository,
    records: list[dict] | None = None,
) -> dict[str, int]:
    """Insert packages and return ``{tracking_number: package_id}``."""
    records = records if records is not None else fixtures.SAMPLE_PACKAGES
    ids: dict[str, int] = {}
    for data in records:
        ids[data["tracking_number"]] = await repo.add_package(**data)
    return ids


async def seed_notifications(
    repo: PackageRepository,
    records: list[dict] | None = None,
    package_ids: dict[str, int] | None = None,
) -> list[int]:
    """Insert notifications, linking package rows when available.

    Any notification whose ``tracking_number`` matches a key in
    ``package_ids`` is linked to that package. The literal sentinel
    ``"Account"`` (auth-failure alerts) is always left unlinked, matching
    the production ``notify_auth_failure`` behaviour.
    """
    records = records if records is not None else fixtures.SAMPLE_NOTIFICATIONS
    package_ids = package_ids or {}
    ids: list[int] = []
    for data in records:
        payload = dict(data)
        tracking = payload["tracking_number"]
        payload["package_id"] = (
            None if tracking == "Account" else package_ids.get(tracking)
        )
        ids.append(await repo.add_notification(**payload))
    return ids


async def seed_all(repo: PackageRepository) -> dict:
    """Seed accounts, packages and notifications with the canonical set.

    Returns a dict ``{"accounts": {...}, "packages": {...},
    "notifications": [...]}`` holding the created IDs — handy for follow-up
    assertions or for wiring related fixtures.
    """
    account_ids = await seed_accounts(repo)
    package_ids = await seed_packages(repo)
    notif_ids = await seed_notifications(repo, package_ids=package_ids)
    return {
        "accounts": account_ids,
        "packages": package_ids,
        "notifications": notif_ids,
    }
