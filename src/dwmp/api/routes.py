from importlib.metadata import version as pkg_version
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel

from dwmp.api.auth import create_token, verify_password
from dwmp.api.dependencies import get_tracking_service
from dwmp.carriers.base import CarrierAuthError
from dwmp.services.tracking import TrackingService

router = APIRouter(prefix="/api/v1")

GITHUB_REPO = "stevendejongnl/dude-wheres-my-package"


# --- Request models ---

class AddPackageRequest(BaseModel):
    tracking_number: str
    carrier: str
    label: str | None = None
    postal_code: str | None = None


class OAuthStartRequest(BaseModel):
    carrier: str
    callback_url: str
    lookback_days: int = 30


class OAuthCallbackRequest(BaseModel):
    carrier: str
    code: str
    callback_url: str
    lookback_days: int = 30


class CredentialsRequest(BaseModel):
    carrier: str
    username: str
    password: str
    totp_secret: str | None = None
    lookback_days: int = 30


class ManualTokenRequest(BaseModel):
    carrier: str
    access_token: str
    refresh_token: str | None = None
    lookback_days: int = 30
    user_agent: str | None = None


class AuthTokenRequest(BaseModel):
    password: str


# --- Auth endpoints ---


@router.post("/auth/token")
async def get_auth_token(body: AuthTokenRequest) -> dict:
    if not verify_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": create_token()}


# --- Carrier endpoints ---

MANUAL_TOKEN_HELP = (
    "Log in at the carrier's website, open browser DevTools (F12) > "
    "Network tab, look for API requests with an Authorization header, "
    "and use POST /api/v1/accounts/token with the Bearer token value."
)


@router.get("/carriers")
async def list_carriers(
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    carriers = []
    for name in service.list_carriers():
        carrier = service.get_carrier(name)
        entry: dict = {
            "name": name,
            "auth_type": carrier.auth_type if carrier else "unknown",
        }
        if carrier and carrier.auth_type == "manual_token":
            entry["auth_hint"] = MANUAL_TOKEN_HELP
        carriers.append(entry)
    return carriers


# --- Account endpoints ---

@router.post("/accounts/oauth/start")
async def oauth_start(
    body: OAuthStartRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        return await service.connect_account_oauth(body.carrier, body.callback_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/oauth/callback", status_code=201)
async def oauth_callback(
    body: OAuthCallbackRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        return await service.handle_oauth_callback(
            body.carrier, body.code, body.callback_url, body.lookback_days
        )
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/test/credentials")
async def test_credentials(
    body: CredentialsRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        await service.validate_account_credentials(
            body.carrier, body.username, body.password,
            totp_secret=body.totp_secret,
        )
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/accounts/test/token")
async def test_token(
    body: ManualTokenRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        await service.validate_account_manual_token(
            body.carrier, body.access_token, body.refresh_token,
            user_agent=body.user_agent,
        )
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/accounts/credentials", status_code=201)
async def connect_credentials(
    body: CredentialsRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        return await service.connect_account_credentials(
            body.carrier, body.username, body.password, body.lookback_days,
            totp_secret=body.totp_secret,
        )
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/token", status_code=201)
async def connect_manual_token(
    body: ManualTokenRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        return await service.connect_account_manual_token(
            body.carrier, body.access_token, body.refresh_token, body.lookback_days,
            user_agent=body.user_agent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/accounts")
async def list_accounts(
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    accounts = await service.list_accounts()
    # Strip tokens from response for security
    for account in accounts:
        account.pop("tokens", None)
    return accounts


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    account = await service.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.pop("tokens", None)
    return account


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(
    account_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> Response:
    deleted = await service.delete_account(account_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Account not found")
    return Response(status_code=204)


@router.post("/accounts/{account_id}/sync")
async def sync_account(
    account_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    try:
        return await service.sync_account(account_id)
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class BrowserPushRequest(BaseModel):
    html: str


class UniversalBrowserPushRequest(BaseModel):
    html: str
    url: str


# URL hostname → carrier name mapping for universal browser-push.
_URL_CARRIER_MAP: list[tuple[str, str]] = [
    ("amazon.nl", "amazon"),
    ("amazon.com", "amazon"),
    ("amazon.de", "amazon"),
    ("dpdgroup.com", "dpd"),
    ("dpd.nl", "dpd"),
    ("postnl.nl", "postnl"),
    ("dhlecommerce.nl", "dhl"),
    ("dhl.nl", "dhl"),
    ("dhl.com", "dhl"),
]


@router.post("/browser-push")
async def universal_browser_push(
    body: UniversalBrowserPushRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    """Universal browser-push: detect carrier from URL, find account, sync.

    The universal bookmarklet sends {html, url}. We map the URL to a carrier,
    find the account, and sync.
    """
    from urllib.parse import urlparse
    hostname = urlparse(body.url).hostname or ""

    carrier_name = None
    for domain, carrier in _URL_CARRIER_MAP:
        if hostname == domain or hostname.endswith("." + domain):
            carrier_name = carrier
            break

    if not carrier_name:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown carrier site: {hostname}. "
            f"Supported: Amazon, DPD, PostNL, DHL.",
        )

    account = await service.find_account_by_carrier(carrier_name)
    if not account:
        raise HTTPException(
            status_code=404,
            detail=f"No {carrier_name} account connected. "
            f"Add one in the Accounts page first.",
        )

    try:
        return await service.sync_account_from_html(account["id"], body.html)
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/browser-push")
async def browser_push(
    account_id: int,
    body: BrowserPushRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    """Accept raw HTML captured by the user's browser and sync from it.

    Called by the browser-push relay page (same-origin) after receiving
    the HTML via postMessage from the bookmarklet on the carrier site.
    """
    try:
        return await service.sync_account_from_html(account_id, body.html)
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- Package endpoints ---

@router.post("/packages", status_code=201)
async def add_package(
    body: AddPackageRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        return await service.add_package(
            tracking_number=body.tracking_number,
            carrier=body.carrier,
            label=body.label,
            postal_code=body.postal_code,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="Package already tracked")


@router.get("/packages")
async def list_packages(
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    return await service.list_packages()


@router.get("/packages/{package_id}")
async def get_package(
    package_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    pkg = await service.get_package(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return pkg


@router.delete("/packages/{package_id}", status_code=204)
async def delete_package(
    package_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> Response:
    deleted = await service.delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Package not found")
    return Response(status_code=204)


@router.post("/packages/{package_id}/refresh")
async def refresh_package(
    package_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    pkg = await service.refresh_package(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return pkg


# --- Notification endpoints ---

@router.get("/notifications")
async def list_notifications(
    limit: int = 50,
    offset: int = 0,
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    return await service.list_notifications(limit, offset)


@router.get("/notifications/unread-count")
async def unread_count(
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    count = await service.get_unread_notification_count()
    return {"count": count}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    marked = await service.mark_notification_read(notification_id)
    if not marked:
        raise HTTPException(status_code=404, detail="Notification not found or already read")
    return {"status": "ok"}


@router.post("/notifications/read-all")
async def mark_all_read(
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    count = await service.mark_all_notifications_read()
    return {"marked": count}


# --- Chrome extension auto-update endpoint ---


@router.get("/extension/updates.xml")
async def extension_update_xml(request: Request) -> RawResponse:
    """Chrome extension auto-update manifest.

    Chrome sends ``?x=id%3D<ext_id>%26v%3D<version>%26...``.
    We echo the extension ID back and always advertise the current
    server version — Chrome decides whether to install it.
    """
    version = pkg_version("dude-wheres-my-package")
    crx_url = (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"v{version}/dwmp-chrome-extension-{version}.crx"
    )

    # Parse extension ID from Chrome's query string
    appid = "extension"
    x_param = request.query_params.get("x", "")
    if x_param:
        parsed = parse_qs(x_param)
        appid = parsed.get("id", [appid])[0]

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">\n'
        f'  <app appid="{appid}">\n'
        f'    <updatecheck crbversion="*" version="{version}" src="{crx_url}"/>\n'
        "  </app>\n"
        "</gupdate>\n"
    )
    return RawResponse(content=xml, media_type="application/xml")
