"""Render static HTML mockups from the real FastAPI templates.

Each mockup is produced by:

1. Creating a throwaway SQLite DB seeded with :mod:`dwmp.testing.fixtures`
   (plus a few timeline events so the expanded-card view looks real);
2. Hitting the same in-process HTTP routes a browser would via
   ``httpx.AsyncClient`` with an ASGI transport;
3. Post-processing the HTML — stripping the HTMX CDN (it can't serve
   responses offline) and, for the notifications / timeline views, doing
   small mutations to reveal state that's normally toggled by a click.

We deliberately avoid bypassing the template layer: rendering through the
real FastAPI app guarantees the mockups stay byte-identical to production.
If the card redesign evolves, the gh-pages mockups update automatically on
the next release.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from dwmp.storage.repository import PackageRepository
from dwmp.testing import seed_all

# Extra tracking events injected after seeding so the timeline mockup
# (expanded package card) renders a rich multi-step history instead of
# "never synced". Keyed by tracking_number; the package must already be
# present in :data:`dwmp.testing.SAMPLE_PACKAGES`.
_TIMELINE_EVENTS: dict[str, list[dict]] = {
    "3SKABA000000001": [
        {
            "status": "pre_transit",
            "description": "Shipment announced to PostNL",
            "location": "bol.com — Waalwijk",
            "offset_hours": -72,
        },
        {
            "status": "pre_transit",
            "description": "Accepted at sorting centre",
            "location": "PostNL Gilze-Rijen",
            "offset_hours": -52,
        },
        {
            "status": "in_transit",
            "description": "Sorted — on its way",
            "location": "PostNL Nieuwegein",
            "offset_hours": -28,
        },
        {
            "status": "in_transit",
            "description": "Arrived at distribution centre",
            "location": "PostNL Amsterdam",
            "offset_hours": -4,
        },
        {
            "status": "out_for_delivery",
            "description": "Courier is on the way",
            "location": "Amsterdam 1012",
            # Stay at ~now so the timeline package sorts first in the
            # Active list — the mockup needs the expanded card visible in
            # the gh-pages iframe viewport.
            "offset_hours": 0,
        },
    ],
}


async def _seed_with_events(repo: PackageRepository) -> dict:
    """Seed the canonical set and append timeline events for the demo pkg."""
    ids = await seed_all(repo)
    now = datetime.now(UTC)
    for tracking, events in _TIMELINE_EVENTS.items():
        pkg_id = ids["packages"].get(tracking)
        if pkg_id is None:
            continue
        for event in events:
            await repo.add_event(
                package_id=pkg_id,
                timestamp=now + timedelta(hours=event["offset_hours"]),
                status=event["status"],
                description=event["description"],
                location=event["location"],
            )
        # Reflect the final event in the package row so the summary chip
        # and active/delivered sorting match the timeline.
        await repo.update_status(pkg_id, events[-1]["status"])
    return ids


def _strip_runtime_scripts(html: str) -> str:
    """Remove scripts + triggers that would flap against a missing backend.

    The static mockups don't ship a JSON API — HTMX would spin retrying the
    badge endpoint forever, and the push-notifications glue would throw.
    Easier to strip both than polyfill.
    """
    html = re.sub(
        r'<script[^>]*src="https://unpkg\.com/htmx\.org@[^"]+"[^>]*>\s*</script>',
        "",
        html,
    )
    html = re.sub(
        r'<script[^>]*src="[^"]*/static/js/notifications\.js"[^>]*>\s*</script>',
        "",
        html,
    )
    # Any `hx-trigger="load,..."` on the badge or elsewhere fires on page
    # mount — drop it so the static HTML doesn't schedule dead requests.
    html = re.sub(r'\shx-trigger="[^"]*load[^"]*"', "", html)
    return html


# The gh-pages site stores shared images under ``assets/`` while the live
# app serves them from ``/static/``. Iframed from ``mockups/dashboard.html``,
# a relative ``../assets/...`` reference resolves to the landing-site copy.
# ``icon-64.png`` has no gh-pages-side counterpart, so we fall back to the
# 128px variant (browsers scale it down cleanly).
_STATIC_ASSET_REWRITES: tuple[tuple[str, str], ...] = (
    ("/static/apple-touch-icon.png", "../assets/apple-touch-icon.png"),
    ("/static/favicon-32.png", "../assets/favicon-32.png"),
    ("/static/icon-64.png", "../assets/icon-128.png"),
)


def _rewrite_static_assets(html: str) -> str:
    """Redirect ``/static/...`` references to the gh-pages ``assets/`` tree."""
    for live, mockup in _STATIC_ASSET_REWRITES:
        html = html.replace(live, mockup)
    return html


def _splice_drawer_open(page_html: str, drawer_body_html: str) -> str:
    """Pre-open the notifications drawer + overlay for the mockup.

    In the live app the bell icon toggles the drawer via JS and lazy-loads
    the body via HTMX. For a static screenshot we flip the inline
    ``display`` styles to visible and inject the fetched body where HTMX
    would have swapped it.
    """
    page_html = page_html.replace(
        'id="notif-drawer-overlay" class="notif-drawer-overlay" style="display:none"',
        'id="notif-drawer-overlay" class="notif-drawer-overlay" style="display:block"',
    )
    page_html = page_html.replace(
        'id="notif-drawer" class="notif-drawer" style="display:none"',
        'id="notif-drawer" class="notif-drawer" style="display:flex"',
    )
    page_html = re.sub(
        r'(<div id="notif-drawer-body" class="notif-drawer-body">)\s*(</div>)',
        lambda m: f"{m.group(1)}{drawer_body_html}{m.group(2)}",
        page_html,
        count=1,
    )
    return page_html


def _expand_timeline_card(page_html: str) -> str:
    """Expand the package card that has seeded tracking events.

    Targets the first tracking number in :data:`_TIMELINE_EVENTS` rather
    than blindly expanding the first card in the list, so the timeline
    mockup always shows real events (instead of "No tracking events yet"
    when an eventless package happens to sort first).

    Only the first ``card-body collapsed`` *after* the tracking number is
    rewritten — every other card stays collapsed to match the real UX
    where a user has clicked exactly one card open.
    """
    tracking = next(iter(_TIMELINE_EVENTS), None)
    if tracking is None:
        return page_html
    idx = page_html.find(tracking)
    if idx == -1:
        return page_html
    return (
        page_html[:idx]
        + page_html[idx:].replace("card-body collapsed", "card-body", 1)
    )


async def _render(client: AsyncClient, path: str) -> str:
    response = await client.get(path)
    response.raise_for_status()
    return response.text


async def build_all(output_dir: Path) -> list[Path]:
    """Generate every mockup into ``output_dir`` and return the paths written.

    Produced files:

    * ``dashboard.html``     — packages page (``/``) with cards collapsed
    * ``notifications.html`` — packages page with the drawer pre-opened and
      populated from the canonical notification fixtures
    * ``timeline.html``      — packages page with the first card expanded,
      showing metadata and a 5-step tracking timeline

    ``extension.html`` is *not* generated — the Chrome-extension popup is
    client-side rendered (``popup.js`` fetches from the API on mount), so
    staticising it needs a DOM-driven pipeline rather than the server-side
    template render this module performs. The existing hand-crafted mockup
    stays in place for now.
    """
    # Auth middleware redirects to /login unless PASSWORD_HASH is unset.
    # The generator never talks to a real deployment, so clear it for the
    # duration of the run.
    os.environ.pop("PASSWORD_HASH", None)

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_db = output_dir / ".mockups-seed.db"
    if tmp_db.exists():
        tmp_db.unlink()

    repo = PackageRepository(db_path=tmp_db)
    await repo.init()
    try:
        await _seed_with_events(repo)

        # Import + override late so we pass our already-initialised repo
        # to the app rather than having its @lru_cache-backed factory try
        # to connect to the default dwmp.db.
        from dwmp.api.app import app
        from dwmp.api.dependencies import (
            get_repository,
            get_tracking_service,
        )
        from dwmp.carriers.amazon import Amazon
        from dwmp.carriers.dhl import DHL
        from dwmp.carriers.dpd import DPD
        from dwmp.carriers.gls import GLS
        from dwmp.carriers.postnl import PostNL
        from dwmp.services.tracking import TrackingService

        tracking = TrackingService(
            repository=repo,
            carriers={
                "amazon": Amazon(), "postnl": PostNL(), "dhl": DHL(),
                "dpd": DPD(), "gls": GLS(),
            },
        )
        app.dependency_overrides[get_repository] = lambda: repo
        app.dependency_overrides[get_tracking_service] = lambda: tracking

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://mockups",
            ) as client:
                dashboard = _rewrite_static_assets(
                    _strip_runtime_scripts(await _render(client, "/"))
                )
                drawer_body = await _render(client, "/notifications/drawer")
        finally:
            app.dependency_overrides.pop(get_repository, None)
            app.dependency_overrides.pop(get_tracking_service, None)

        notifications = _splice_drawer_open(dashboard, drawer_body)
        timeline = _expand_timeline_card(dashboard)

        written: list[Path] = []
        for name, html in (
            ("dashboard.html", dashboard),
            ("notifications.html", notifications),
            ("timeline.html", timeline),
        ):
            path = output_dir / name
            path.write_text(html, encoding="utf-8")
            written.append(path)
        return written
    finally:
        await repo.close()
        if tmp_db.exists():
            tmp_db.unlink()
