import pytest

from dwmp.storage.repository import PackageRepository
from dwmp.testing import (
    SAMPLE_ACCOUNTS,
    SAMPLE_NOTIFICATIONS,
    SAMPLE_PACKAGES,
    seed_accounts,
    seed_all,
    seed_notifications,
    seed_packages,
)


@pytest.fixture
async def repo(tmp_path):
    r = PackageRepository(db_path=tmp_path / "fixtures.db")
    await r.init()
    yield r
    await r.close()


async def test_seed_all_inserts_every_record(repo):
    ids = await seed_all(repo)

    assert len(ids["accounts"]) == len(SAMPLE_ACCOUNTS)
    assert len(ids["packages"]) == len(SAMPLE_PACKAGES)
    assert len(ids["notifications"]) == len(SAMPLE_NOTIFICATIONS)

    accounts = await repo.list_accounts()
    assert {a["carrier"] for a in accounts} == {
        rec["carrier"] for rec in SAMPLE_ACCOUNTS
    }


async def test_auth_failure_notification_stays_unlinked(repo):
    await seed_all(repo)

    notifs = await repo.list_notifications()
    auth = [n for n in notifs if n["new_status"] == "auth_failed"]

    assert len(auth) == 1
    assert auth[0]["package_id"] is None
    assert auth[0]["tracking_number"] == "Account"


async def test_package_notifications_link_to_seeded_packages(repo):
    ids = await seed_all(repo)

    notifs = await repo.list_notifications()
    linked = [n for n in notifs if n["new_status"] != "auth_failed"]
    pkg_ids = set(ids["packages"].values())

    assert len(linked) == len(SAMPLE_NOTIFICATIONS) - 1
    for n in linked:
        assert n["package_id"] in pkg_ids


async def test_seeders_accept_custom_records(repo):
    await seed_accounts(repo, [])
    await seed_packages(repo, [])
    notif_ids = await seed_notifications(
        repo,
        records=[
            {
                "tracking_number": "CUSTOM1",
                "carrier": "dhl",
                "label": "Custom test",
                "old_status": "pre_transit",
                "new_status": "in_transit",
                "description": "Custom notif",
            }
        ],
    )

    assert len(notif_ids) == 1
    notifs = await repo.list_notifications()
    assert len(notifs) == 1
    assert notifs[0]["tracking_number"] == "CUSTOM1"
    assert notifs[0]["package_id"] is None


async def test_seed_notifications_links_when_package_ids_given(repo):
    pkg_ids = await seed_packages(repo)
    notif_ids = await seed_notifications(repo, package_ids=pkg_ids)

    assert len(notif_ids) == len(SAMPLE_NOTIFICATIONS)
    notifs = await repo.list_notifications()
    linked = {
        n["tracking_number"]: n["package_id"]
        for n in notifs
        if n["new_status"] != "auth_failed"
    }
    for tracking, package_id in linked.items():
        assert package_id == pkg_ids[tracking]
