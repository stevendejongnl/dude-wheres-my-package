from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from dwmp.api.dependencies import get_tracking_service
from dwmp.carriers.base import CarrierAuthError
from dwmp.services.tracking import TrackingService

router = APIRouter(prefix="/api/v1")


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
    lookback_days: int = 30


# --- Carrier endpoints ---

@router.get("/carriers")
async def list_carriers(
    service: TrackingService = Depends(get_tracking_service),
) -> list[dict]:
    carriers = []
    for name in service.list_carriers():
        carrier = service.get_carrier(name)
        carriers.append({
            "name": name,
            "auth_type": carrier.auth_type if carrier else "unknown",
        })
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


@router.post("/accounts/credentials", status_code=201)
async def connect_credentials(
    body: CredentialsRequest,
    service: TrackingService = Depends(get_tracking_service),
) -> dict:
    try:
        return await service.connect_account_credentials(
            body.carrier, body.username, body.password, body.lookback_days
        )
    except CarrierAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
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
