from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from importlib.metadata import version as pkg_version

from dwmp.api.auth import is_authenticated, login_response, logout_response, verify_password
from dwmp.api.dependencies import get_tracking_service
from dwmp.services.tracking import TrackingService

VERSION = pkg_version("dude-wheres-my-package")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


def _require_auth(request: Request):
    if not is_authenticated(request):
        raise _LoginRequired()


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

    # Sender: first pre_transit event description
    sender = None
    for e in events:
        if e.get("status") == "pre_transit" and e.get("description"):
            sender = e["description"]
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
    _require_auth(request)

    packages = await service.list_packages()
    for pkg in packages:
        full = await service.get_package(pkg["id"])
        if full:
            pkg["events"] = full.get("events", [])
        _enrich_package(pkg)

    active = [p for p in packages if p["current_status"] not in ("delivered", "returned")]
    delivered = [p for p in packages if p["current_status"] in ("delivered", "returned")]

    accounts = await service.list_accounts()

    return templates.TemplateResponse(
        request, "packages.html",
        {"active_nav": "packages", "active": active, "delivered": delivered, "accounts": len(accounts), "version": VERSION},
    )


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(
    request: Request,
    service: TrackingService = Depends(get_tracking_service),
):
    _require_auth(request)

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

    return templates.TemplateResponse(
        request, "accounts.html",
        {"active_nav": "accounts", "active": "accounts", "accounts": accounts, "carriers": carriers, "version": VERSION},
    )
