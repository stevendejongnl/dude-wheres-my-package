from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dwmp.api.dependencies import get_tracking_service
from dwmp.services.tracking import TrackingService

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def packages_page(
    request: Request,
    service: TrackingService = Depends(get_tracking_service),
):
    packages = await service.list_packages()
    # Attach events to each package
    for pkg in packages:
        full = await service.get_package(pkg["id"])
        if full:
            pkg["events"] = full.get("events", [])

    accounts = await service.list_accounts()

    return templates.TemplateResponse(
        request, "packages.html",
        {"active": "packages", "packages": packages, "accounts": accounts},
    )


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

    return templates.TemplateResponse(
        request, "accounts.html",
        {"active": "accounts", "accounts": accounts, "carriers": carriers},
    )
