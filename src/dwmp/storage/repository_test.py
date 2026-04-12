import pytest
from datetime import datetime, timedelta, timezone

from dwmp.storage.repository import PackageRepository


@pytest.fixture
async def repo(tmp_path):
    r = PackageRepository(db_path=tmp_path / "test.db")
    await r.init()
    yield r
    await r.close()


async def test_add_and_get_package(repo: PackageRepository):
    pkg_id = await repo.add_package(
        tracking_number="3STEST123456",
        carrier="postnl",
        label="New headphones",
        postal_code="1234AB",
    )

    pkg = await repo.get_package(pkg_id)
    assert pkg is not None
    assert pkg["tracking_number"] == "3STEST123456"
    assert pkg["carrier"] == "postnl"
    assert pkg["label"] == "New headphones"
    assert pkg["postal_code"] == "1234AB"
    assert pkg["current_status"] == "unknown"


async def test_list_packages(repo: PackageRepository):
    await repo.add_package(tracking_number="AAA", carrier="postnl")
    await repo.add_package(tracking_number="BBB", carrier="dhl")

    packages = await repo.list_packages()
    assert len(packages) == 2
    numbers = {p["tracking_number"] for p in packages}
    assert numbers == {"AAA", "BBB"}


async def test_delete_package(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="DEL1", carrier="dpd")
    deleted = await repo.delete_package(pkg_id)
    assert deleted is True

    pkg = await repo.get_package(pkg_id)
    assert pkg is None


async def test_delete_nonexistent_package(repo: PackageRepository):
    deleted = await repo.delete_package(999)
    assert deleted is False


async def test_update_status(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="UPD1", carrier="postnl")
    await repo.update_status(pkg_id, "in_transit")

    pkg = await repo.get_package(pkg_id)
    assert pkg["current_status"] == "in_transit"


async def test_add_and_get_events(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="EVT1", carrier="dhl")

    await repo.add_event(
        package_id=pkg_id,
        timestamp=datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc),
        status="in_transit",
        description="Package is on its way",
        location="Amsterdam",
    )
    await repo.add_event(
        package_id=pkg_id,
        timestamp=datetime(2026, 4, 11, 14, 0, tzinfo=timezone.utc),
        status="out_for_delivery",
        description="Out for delivery",
        location="Utrecht",
    )

    events = await repo.get_events(pkg_id)
    assert len(events) == 2
    assert events[0]["status"] == "in_transit"
    assert events[1]["status"] == "out_for_delivery"


async def test_duplicate_event_is_ignored(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="DUP1", carrier="postnl")
    ts = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)

    await repo.add_event(pkg_id, ts, "in_transit", "On its way")
    await repo.add_event(pkg_id, ts, "in_transit", "On its way")

    events = await repo.get_events(pkg_id)
    assert len(events) == 1


async def test_duplicate_package_raises(repo: PackageRepository):
    await repo.add_package(tracking_number="SAME1", carrier="postnl")
    with pytest.raises(ValueError, match="already tracked"):
        await repo.add_package(tracking_number="SAME1", carrier="postnl")


# --- Account tests ---


async def test_add_and_get_account(repo: PackageRepository):
    account_id = await repo.add_account(
        carrier="postnl",
        auth_type="oauth",
        tokens={"access_token": "abc123", "refresh_token": "ref456"},
        lookback_days=14,
    )

    account = await repo.get_account(account_id)
    assert account is not None
    assert account["carrier"] == "postnl"
    assert account["auth_type"] == "oauth"
    assert account["tokens"]["access_token"] == "abc123"
    assert account["lookback_days"] == 14
    assert account["status"] == "connected"


async def test_list_accounts(repo: PackageRepository):
    await repo.add_account(carrier="postnl", auth_type="oauth")
    await repo.add_account(carrier="dpd", auth_type="credentials", username="user1")

    accounts = await repo.list_accounts()
    assert len(accounts) == 2


async def test_delete_account(repo: PackageRepository):
    account_id = await repo.add_account(carrier="dhl", auth_type="oauth")
    deleted = await repo.delete_account(account_id)
    assert deleted is True
    assert await repo.get_account(account_id) is None


async def test_update_account_tokens(repo: PackageRepository):
    account_id = await repo.add_account(
        carrier="postnl", auth_type="oauth",
        tokens={"access_token": "old"},
    )
    await repo.update_account_tokens(account_id, {"access_token": "new", "refresh_token": "ref"})

    account = await repo.get_account(account_id)
    assert account["tokens"]["access_token"] == "new"


async def test_update_account_status(repo: PackageRepository):
    account_id = await repo.add_account(carrier="postnl", auth_type="oauth")
    await repo.update_account_status(
        account_id, "auth_failed",
        "Login flow changed. Please re-authenticate or check for carrier API updates.",
    )

    account = await repo.get_account(account_id)
    assert account["status"] == "auth_failed"
    assert "re-authenticate" in account["status_message"]


async def test_add_package_with_account(repo: PackageRepository):
    account_id = await repo.add_account(carrier="postnl", auth_type="oauth")
    pkg_id = await repo.add_package(
        tracking_number="ACCT1", carrier="postnl",
        account_id=account_id, source="account",
    )

    pkg = await repo.get_package(pkg_id)
    assert pkg["account_id"] == account_id
    assert pkg["source"] == "account"


async def test_list_packages_by_account(repo: PackageRepository):
    account_id = await repo.add_account(carrier="postnl", auth_type="oauth")
    await repo.add_package(tracking_number="A1", carrier="postnl", account_id=account_id, source="account")
    await repo.add_package(tracking_number="A2", carrier="postnl", account_id=account_id, source="account")
    await repo.add_package(tracking_number="M1", carrier="postnl")  # manual, no account

    by_account = await repo.list_packages_by_account(account_id)
    assert len(by_account) == 2

    all_pkgs = await repo.list_packages()
    assert len(all_pkgs) == 3


async def test_find_package(repo: PackageRepository):
    await repo.add_package(tracking_number="FIND1", carrier="dhl")

    found = await repo.find_package("FIND1", "dhl")
    assert found is not None
    assert found["tracking_number"] == "FIND1"

    not_found = await repo.find_package("NOPE", "dhl")
    assert not_found is None


# --- Notification tests ---


async def test_add_notification(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="NOTIF1", carrier="postnl")
    notif_id = await repo.add_notification(
        package_id=pkg_id,
        old_status="unknown",
        new_status="in_transit",
        tracking_number="NOTIF1",
        carrier="postnl",
        label="Test label",
    )
    assert notif_id > 0

    notifications = await repo.list_notifications()
    assert len(notifications) == 1
    assert notifications[0]["old_status"] == "unknown"
    assert notifications[0]["new_status"] == "in_transit"
    assert notifications[0]["tracking_number"] == "NOTIF1"
    assert notifications[0]["label"] == "Test label"
    assert notifications[0]["is_read"] == 0


async def test_get_unread_count(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="CNT1", carrier="dhl")
    await repo.add_notification(pkg_id, "unknown", "in_transit", "CNT1", "dhl")
    await repo.add_notification(pkg_id, "in_transit", "delivered", "CNT1", "dhl")

    assert await repo.get_unread_count() == 2

    await repo.mark_notification_read(1)
    assert await repo.get_unread_count() == 1


async def test_list_notifications_ordered(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="ORD1", carrier="postnl")
    await repo.add_notification(pkg_id, "unknown", "pre_transit", "ORD1", "postnl")
    await repo.add_notification(pkg_id, "pre_transit", "in_transit", "ORD1", "postnl")
    await repo.add_notification(pkg_id, "in_transit", "delivered", "ORD1", "postnl")

    notifications = await repo.list_notifications()
    assert len(notifications) == 3
    # Most recent first
    assert notifications[0]["new_status"] == "delivered"
    assert notifications[2]["new_status"] == "pre_transit"


async def test_list_notifications_limit_offset(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="LIM1", carrier="dhl")
    for i in range(5):
        await repo.add_notification(pkg_id, "s1", f"s{i+2}", "LIM1", "dhl")

    page1 = await repo.list_notifications(limit=2, offset=0)
    assert len(page1) == 2

    page2 = await repo.list_notifications(limit=2, offset=2)
    assert len(page2) == 2

    page3 = await repo.list_notifications(limit=2, offset=4)
    assert len(page3) == 1


async def test_mark_notification_read(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="MRK1", carrier="postnl")
    await repo.add_notification(pkg_id, "unknown", "in_transit", "MRK1", "postnl")

    marked = await repo.mark_notification_read(1)
    assert marked is True

    # Already read — should return False
    marked_again = await repo.mark_notification_read(1)
    assert marked_again is False


async def test_mark_all_read(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="ALL1", carrier="dhl")
    await repo.add_notification(pkg_id, "unknown", "in_transit", "ALL1", "dhl")
    await repo.add_notification(pkg_id, "in_transit", "delivered", "ALL1", "dhl")

    count = await repo.mark_all_read()
    assert count == 2
    assert await repo.get_unread_count() == 0


async def test_delete_package_cascades_notifications(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="CAS1", carrier="postnl")
    await repo.add_notification(pkg_id, "unknown", "in_transit", "CAS1", "postnl")

    assert len(await repo.list_notifications()) == 1

    await repo.delete_package(pkg_id)
    assert len(await repo.list_notifications()) == 0


async def test_delete_old_notifications(repo: PackageRepository):
    pkg_id = await repo.add_package(tracking_number="OLD1", carrier="dhl")
    await repo.add_notification(pkg_id, "unknown", "in_transit", "OLD1", "dhl")

    # Delete notifications older than 0 days (everything)
    deleted = await repo.delete_old_notifications(days=0)
    assert deleted == 1
    assert len(await repo.list_notifications()) == 0
