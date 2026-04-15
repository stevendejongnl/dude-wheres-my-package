from pathlib import Path

from dwmp.mockups import build_all


async def test_build_all_writes_three_mockups(tmp_path: Path):
    written = await build_all(tmp_path)

    names = {path.name for path in written}
    assert names == {"dashboard.html", "notifications.html", "timeline.html"}
    for path in written:
        assert path.exists(), f"missing {path}"
        assert path.stat().st_size > 0, f"empty {path}"


async def test_dashboard_has_every_sample_package(tmp_path: Path):
    await build_all(tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")

    # Each seeded tracking number should show up on the page.
    for tracking in (
        "403-2026068-5498762",
        "3SKABA000000001",
        "00340123450000000012",
        "05096789012345",
    ):
        assert tracking in html, f"tracking {tracking} missing from dashboard"


async def test_notifications_mockup_has_drawer_open(tmp_path: Path):
    await build_all(tmp_path)
    html = (tmp_path / "notifications.html").read_text(encoding="utf-8")

    # Drawer + overlay must be flipped visible, and the alert-variant card
    # (the DPD auth-failure fixture) must be in the drawer body.
    assert 'class="notif-drawer-overlay" style="display:block"' in html
    assert 'class="notif-drawer" style="display:flex"' in html
    assert "notification-card--alert" in html
    assert "DPD account sync failed" in html


async def test_timeline_mockup_expands_first_card(tmp_path: Path):
    await build_all(tmp_path)
    dashboard = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    timeline = (tmp_path / "timeline.html").read_text(encoding="utf-8")

    # Exactly one "card-body collapsed" should have been de-collapsed.
    assert dashboard.count("card-body collapsed") - 1 == (
        timeline.count("card-body collapsed")
    )
    # And the seeded event descriptions should be visible in the expanded
    # card's timeline.
    assert "Shipment announced to PostNL" in timeline
    assert "Courier is on the way" in timeline


async def test_htmx_cdn_is_stripped(tmp_path: Path):
    await build_all(tmp_path)
    for name in ("dashboard.html", "notifications.html", "timeline.html"):
        html = (tmp_path / name).read_text(encoding="utf-8")
        assert "unpkg.com/htmx.org" not in html, name
        assert "notifications.js" not in html, name


async def test_static_asset_paths_rewritten_for_gh_pages(tmp_path: Path):
    """Mockups are iframed from ``gh-pages/mockups/dashboard.html``.

    The live app's ``/static/...`` absolute paths would 404 there — the
    generator must repoint them at the gh-pages-side ``assets/`` folder.
    """
    await build_all(tmp_path)
    for name in ("dashboard.html", "notifications.html", "timeline.html"):
        html = (tmp_path / name).read_text(encoding="utf-8")
        assert "/static/icon-64.png" not in html, name
        assert "/static/apple-touch-icon.png" not in html, name
        assert "/static/favicon-32.png" not in html, name
        assert "../assets/icon-128.png" in html, name
