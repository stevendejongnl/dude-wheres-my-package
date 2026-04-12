from datetime import datetime
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dwmp.api.auth import login_response, logout_response, verify_password
from dwmp.api.dependencies import get_tracking_service
from dwmp.services.tracking import TrackingService

VERSION = pkg_version("dude-wheres-my-package")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


class _LoginRequired(Exception):
    pass


def _format_time(ts_str: str) -> str:
    """Format ISO timestamp to human-readable."""
    try:
        dt = datetime.fromisoformat(ts_str)
        now = datetime.now(dt.tzinfo)
        diff = now - dt

        if diff.days == 0:
            return f"Today {dt.strftime('%H:%M')}"
        elif diff.days == 1:
            return f"Yesterday {dt.strftime('%H:%M')}"
        elif diff.days < 7:
            return dt.strftime("%A %H:%M")
        else:
            return dt.strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return ts_str[:16].replace("T", " ") if ts_str else ""


def _enrich_package(pkg: dict) -> dict:
    """Add computed fields for display."""
    events = pkg.get("events", [])

    # Sender: first pre_transit event that looks like a name, not a date or status text
    sender = None
    _skip_phrases = ("exchanging data", "data received", "aangekondigd", "aangemeld")
    for e in events:
        if e.get("status") == "pre_transit" and e.get("description"):
            desc = e["description"].strip()
            # Skip date-like descriptions (e.g., Amazon's "12 april 2026")
            if desc and desc[0].isdigit():
                continue
            # Skip tracking status descriptions (e.g., DPD's "Exchanging data internally")
            lower = desc.lower()
            if any(kw in lower for kw in _skip_phrases):
                continue
            sender = desc
            break

    # Last update time
    last_update = ""
    if events:
        last_ts = events[-1].get("timestamp", "")
        last_update = _format_time(last_ts)
    elif pkg.get("updated_at"):
        last_update = _format_time(pkg["updated_at"])

    # Format event times
    for event in events:
        event["formatted_time"] = _format_time(event.get("timestamp", ""))

    pkg["sender"] = sender
    pkg["last_update"] = last_update
    return pkg


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"active_nav": "", "error": None, "version": VERSION},
    )


@router.post("/login")
async def login_submit(request: Request, password: str = Form()):
    if verify_password(password):
        return login_response("/")
    return templates.TemplateResponse(
        request, "login.html",
        {"active_nav": "", "error": "Wrong password", "version": VERSION},
        status_code=401,
    )


@router.get("/logout")
async def logout():
    return logout_response()


@router.get("/", response_class=HTMLResponse)
async def packages_page(
    request: Request,
    service: TrackingService = Depends(get_tracking_service),
):

    packages = await service.list_packages()
    for pkg in packages:
        full = await service.get_package(pkg["id"])
        if full:
            pkg["events"] = full.get("events", [])
        _enrich_package(pkg)

    def _last_event_ts(pkg: dict) -> str:
        events = pkg.get("events", [])
        if events:
            return events[-1].get("timestamp", "")
        return pkg.get("updated_at", "")

    active = sorted(
        [p for p in packages if p["current_status"] not in ("delivered", "returned")],
        key=_last_event_ts, reverse=True,
    )
    delivered = sorted(
        [p for p in packages if p["current_status"] in ("delivered", "returned")],
        key=_last_event_ts, reverse=True,
    )

    accounts = await service.list_accounts()

    ctx = {
        "active_nav": "packages", "active": active, "delivered": delivered,
        "accounts": len(accounts), "version": VERSION,
    }
    return templates.TemplateResponse(request, "packages.html", ctx)


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(
    request: Request,
    service: TrackingService = Depends(get_tracking_service),
):

    accounts = await service.list_accounts()
    for account in accounts:
        account.pop("tokens", None)

    carriers = []
    for name in service.list_carriers():
        carrier = service.get_carrier(name)
        entry = {"name": name, "auth_type": carrier.auth_type if carrier else "unknown"}
        if carrier and carrier.auth_type == "manual_token":
            entry["auth_hint"] = (
                "Requires browser login + token capture. See docs for instructions."
            )
        carriers.append(entry)

    ctx = {
        "active_nav": "accounts", "active": "accounts",
        "accounts": accounts, "carriers": carriers, "version": VERSION,
    }
    return templates.TemplateResponse(request, "accounts.html", ctx)


# --- Notification views ---


def _format_status(status: str) -> str:
    """Format status value for display."""
    return status.replace("_", " ").title()


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    service: TrackingService = Depends(get_tracking_service),
):
    notifications = await service.list_notifications(limit=100)
    for n in notifications:
        n["formatted_time"] = _format_time(n.get("created_at", ""))
        n["old_status_display"] = _format_status(n["old_status"])
        n["new_status_display"] = _format_status(n["new_status"])
    unread_count = await service.get_unread_notification_count()

    # Auto-mark all as read on page visit
    if unread_count > 0:
        await service.mark_all_notifications_read()

    ctx = {
        "active_nav": "notifications", "notifications": notifications,
        "unread_count": unread_count, "version": VERSION,
    }
    return templates.TemplateResponse(
        request, "notifications.html", ctx,
    )


@router.get("/notifications/badge", response_class=HTMLResponse)
async def notification_badge(
    service: TrackingService = Depends(get_tracking_service),
):
    count = await service.get_unread_notification_count()
    if count > 0:
        display = "99+" if count > 99 else str(count)
        return HTMLResponse(
            f'<span class="notif-badge-dot" data-count="{count}">{display}</span>'
        )
    return HTMLResponse('<span data-count="0"></span>')


@router.post("/notifications/{notification_id}/read", response_class=HTMLResponse)
async def mark_notification_read_view(
    request: Request,
    notification_id: int,
    service: TrackingService = Depends(get_tracking_service),
):
    await service.mark_notification_read(notification_id)
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.post("/notifications/read-all", response_class=HTMLResponse)
async def mark_all_read_view(
    service: TrackingService = Depends(get_tracking_service),
):
    await service.mark_all_notifications_read()
    return HTMLResponse("", headers={"HX-Refresh": "true"})
