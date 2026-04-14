import os
from datetime import UTC, datetime
from importlib.metadata import version as pkg_version
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dwmp.api.auth import login_response, logout_response, verify_password
from dwmp.api.dependencies import get_tracking_service
from dwmp.carriers.base import CarrierAuthError
from dwmp.services.tracking import TrackingService

VERSION = pkg_version("dude-wheres-my-package")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


class _LoginRequired(Exception):
    pass


def _base_path(request: Request) -> str:
    """Return the reverse-proxy prefix captured by IngressPathMiddleware.

    Read from ``request.state`` rather than ``scope["root_path"]`` because
    setting ``root_path`` breaks Starlette's Mount routing for sub-apps like
    ``StaticFiles``. See ``IngressPathMiddleware`` in ``app.py`` for the full
    explanation.
    """
    return getattr(request.state, "ingress_path", "")


_DISPLAY_TZ = ZoneInfo(os.environ.get("TZ", "Europe/Amsterdam"))


def _format_time(ts_str: str) -> str:
    """Format ISO timestamp to human-readable in the configured timezone."""
    try:
        dt = datetime.fromisoformat(ts_str)
        # Assume UTC if no timezone info, then convert to display timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt = dt.astimezone(_DISPLAY_TZ)
        now = datetime.now(_DISPLAY_TZ)
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

    # First event date
    first_event_date = ""
    if events:
        first_event_date = _format_time(events[0].get("timestamp", ""))

    pkg["sender"] = sender
    pkg["first_event_date"] = first_event_date
    pkg["last_update"] = last_update
    return pkg


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request, "login.html",
        {"active_nav": "", "error": None, "version": VERSION, "base_path": _base_path(request)},
    )


@router.post("/login")
async def login_submit(request: Request, password: str = Form()):
    if verify_password(password):
        return login_response(f"{_base_path(request)}/")
    return templates.TemplateResponse(
        request, "login.html",
        {
            "active_nav": "",
            "error": "Wrong password",
            "version": VERSION,
            "base_path": _base_path(request),
        },
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    return logout_response(_base_path(request))


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
        "base_path": _base_path(request),
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
        "base_path": _base_path(request),
    }
    return templates.TemplateResponse(request, "accounts.html", ctx)


# --- Add-account form lifecycle (HTMX partials) ---

# carrier name → form template
_FORM_TEMPLATES = {
    "amazon": "account_form_credentials.html",
    "dhl": "account_form_credentials.html",
    "postnl": "account_form_postnl.html",
    "dpd": "account_form_dpd.html",
}


def _form_template(carrier: str) -> str:
    template = _FORM_TEMPLATES.get(carrier)
    if template is None:
        raise HTTPException(status_code=404, detail=f"No add form for {carrier}")
    return template


@router.get("/accounts/add/{carrier}", response_class=HTMLResponse)
async def add_account_form(
    request: Request,
    carrier: str,
    service: TrackingService = Depends(get_tracking_service),
):
    template = _form_template(carrier)
    if service.get_carrier(carrier) is None:
        raise HTTPException(status_code=404, detail=f"Unknown carrier: {carrier}")
    ctx = {"carrier": carrier, "base_path": _base_path(request)}
    return templates.TemplateResponse(request, template, ctx)


@router.get("/accounts/add/{carrier}/cancel", response_class=HTMLResponse)
async def add_account_form_cancel(carrier: str):
    """Empty response — used to clear the inline form via HTMX swap."""
    return HTMLResponse("")


def _result_html(ok: bool, message: str) -> HTMLResponse:
    cls = "ok" if ok else "error"
    icon = "✓" if ok else "✕"
    return HTMLResponse(
        f'<div class="test-result {cls}"><span class="test-icon">{icon}</span> {message}</div>',
    )


@router.post("/accounts/add/{carrier}/test", response_class=HTMLResponse)
async def add_account_test(
    carrier: str,
    service: TrackingService = Depends(get_tracking_service),
    username: str = Form(default=""),
    password: str = Form(default=""),
    totp_secret: str = Form(default=""),
    access_token: str = Form(default=""),
    refresh_token: str = Form(default=""),
    user_agent: str = Form(default=""),
):
    template = _form_template(carrier)
    try:
        if template == "account_form_credentials.html":
            await service.validate_account_credentials(
                carrier, username, password, totp_secret=totp_secret or None,
            )
        else:
            await service.validate_account_manual_token(
                carrier, access_token, refresh_token or None,
                user_agent=user_agent or None,
            )
    except CarrierAuthError as exc:
        return _result_html(False, exc.message)
    except ValueError as exc:
        return _result_html(False, str(exc))
    return _result_html(True, "Connection works — click Save to add this account.")


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
async def edit_account_form(
    request: Request,
    account_id: int,
    service: TrackingService = Depends(get_tracking_service),
):
    """Render the account form pre-filled with the existing account's values."""
    account = await service.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    template = _form_template(account["carrier"])
    ctx = {
        "carrier": account["carrier"],
        "account": account,
        "base_path": _base_path(request),
    }
    return templates.TemplateResponse(request, template, ctx)


@router.get("/accounts/{account_id}/edit/cancel", response_class=HTMLResponse)
async def edit_account_form_cancel(account_id: int):
    """Empty response — used to clear the inline edit form via HTMX swap."""
    return HTMLResponse("")


@router.post("/accounts/{account_id}/edit/save", response_class=HTMLResponse)
async def edit_account_save(
    request: Request,
    account_id: int,
    service: TrackingService = Depends(get_tracking_service),
    username: str = Form(default=""),
    password: str = Form(default=""),
    totp_secret: str = Form(default=""),
    access_token: str = Form(default=""),
    refresh_token: str = Form(default=""),
    user_agent: str = Form(default=""),
    lookback_days: int = Form(default=30),
):
    account = await service.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    template = _form_template(account["carrier"])
    try:
        if template == "account_form_credentials.html":
            await service.update_account_credentials(
                account_id, account["carrier"], username, password,
                lookback_days, totp_secret=totp_secret or None,
            )
        else:
            await service.update_account_manual_token(
                account_id, account["carrier"], access_token,
                refresh_token or None, lookback_days,
                user_agent=user_agent or None,
            )
    except CarrierAuthError as exc:
        return _result_html(False, exc.message)
    except ValueError as exc:
        return _result_html(False, str(exc))
    return HTMLResponse("", headers={"HX-Refresh": "true"})


@router.post("/accounts/{account_id}/sync", response_class=HTMLResponse)
async def sync_account_view(
    request: Request,
    account_id: int,
    service: TrackingService = Depends(get_tracking_service),
):
    """HTMX endpoint: sync an account and return the refreshed row."""
    sync_result: dict | None = None
    try:
        results = await service.sync_account(account_id)
        sync_result = {"ok": True, "count": len(results), "message": None}
    except CarrierAuthError as exc:
        sync_result = {"ok": False, "count": 0, "message": exc.message}
    except ValueError:
        raise HTTPException(status_code=404, detail="Account not found")

    account = await service.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.pop("tokens", None)

    ctx = {
        "account": account,
        "base_path": _base_path(request),
        "sync_result": sync_result,
    }
    return templates.TemplateResponse(request, "_account_row.html", ctx)


@router.post("/accounts/add/{carrier}/save", response_class=HTMLResponse)
async def add_account_save(
    request: Request,
    carrier: str,
    service: TrackingService = Depends(get_tracking_service),
    username: str = Form(default=""),
    password: str = Form(default=""),
    totp_secret: str = Form(default=""),
    access_token: str = Form(default=""),
    refresh_token: str = Form(default=""),
    user_agent: str = Form(default=""),
    lookback_days: int = Form(default=30),
):
    template = _form_template(carrier)
    try:
        if template == "account_form_credentials.html":
            await service.connect_account_credentials(
                carrier, username, password, lookback_days,
                totp_secret=totp_secret or None,
            )
        else:
            await service.connect_account_manual_token(
                carrier, access_token, refresh_token or None, lookback_days,
                user_agent=user_agent or None,
            )
    except CarrierAuthError as exc:
        return _result_html(False, exc.message)
    except ValueError as exc:
        return _result_html(False, str(exc))
    return HTMLResponse("", headers={"HX-Refresh": "true"})


# --- Track-package modal lifecycle (HTMX partials) ---


@router.get("/packages/add", response_class=HTMLResponse)
async def track_package_form(
    request: Request,
    service: TrackingService = Depends(get_tracking_service),
):
    """Render the 'Track a package' modal."""
    ctx = {
        "carriers": service.list_carriers(),
        "base_path": _base_path(request),
    }
    return templates.TemplateResponse(request, "track_package_form.html", ctx)


@router.get("/packages/add/cancel", response_class=HTMLResponse)
async def track_package_form_cancel():
    """Empty response — closes the modal via HTMX swap."""
    return HTMLResponse("")


@router.post("/packages/add/save", response_class=HTMLResponse)
async def track_package_save(
    service: TrackingService = Depends(get_tracking_service),
    tracking_number: str = Form(default=""),
    carrier: str = Form(default=""),
    label: str = Form(default=""),
    postal_code: str = Form(default=""),
):
    tracking_number = tracking_number.strip()
    carrier = carrier.strip()
    if not tracking_number:
        return _result_html(False, "Tracking number is required.")
    if not carrier:
        return _result_html(False, "Please select a carrier.")
    if service.get_carrier(carrier) is None:
        return _result_html(False, f"Unknown carrier: {carrier}")
    if carrier == "gls" and not postal_code.strip():
        return _result_html(False, "GLS requires a postal code to fetch tracking details.")

    try:
        await service.add_package(
            tracking_number=tracking_number,
            carrier=carrier,
            label=label.strip() or None,
            postal_code=postal_code.strip() or None,
        )
    except ValueError:
        return _result_html(False, "That tracking number is already being tracked.")
    return HTMLResponse("", headers={"HX-Refresh": "true"})


# --- Package refresh view ---


@router.post("/packages/{package_id}/refresh", response_class=HTMLResponse)
async def refresh_package_view(
    request: Request,
    package_id: int,
    service: TrackingService = Depends(get_tracking_service),
):
    """HTMX endpoint: refresh a package via public tracking and return the updated card."""
    pkg = await service.refresh_package(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")
    _enrich_package(pkg)
    ctx = {
        "pkg": pkg,
        "base_path": _base_path(request),
    }
    return templates.TemplateResponse(request, "_package_card.html", ctx)


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
        "base_path": _base_path(request),
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
