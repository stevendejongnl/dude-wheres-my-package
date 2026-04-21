import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dwmp.api.auth import is_authenticated
from dwmp.api.dependencies import get_repository, get_tracking_service
from dwmp.api.routes import router
from dwmp.api.views import _LoginRequired
from dwmp.api.views import router as views_router
from dwmp.services.scheduler import PackageScheduler
from dwmp.services.telegram_notifier import TelegramNotifier

_lifespan_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repo = get_repository()
    await repo.init()

    interval = int(os.environ.get("POLL_INTERVAL_MINUTES", "30"))
    scheduler = PackageScheduler(
        tracking_service=get_tracking_service(),
        interval_minutes=interval,
    )
    scheduler.start()

    notifier = TelegramNotifier()
    await notifier.send_startup(app.version)

    shutdown_reason = "graceful"
    try:
        yield
    except BaseException as exc:
        shutdown_reason = f"error: {type(exc).__name__}"
        try:
            await notifier.send_crash(exc, app.version)
        except Exception as notify_err:
            _lifespan_logger.error("Failed to send crash notification: %s", notify_err)
        raise
    finally:
        scheduler.stop()
        await repo.close()
        try:
            await notifier.send_shutdown(app.version, reason=shutdown_reason)
        except Exception as notify_err:
            _lifespan_logger.error("Failed to send shutdown notification: %s", notify_err)


OPEN_PATHS = {
    "/health", "/login", "/api/v1/auth/token", "/api/v1/extension",
    "/static", "/docs", "/openapi.json", "/redoc",
}


def _root_path(request: Request) -> str:
    """Return the X-Ingress-Path prefix captured by IngressPathMiddleware, no trailing slash."""
    return getattr(request.state, "ingress_path", "")


class IngressPathMiddleware(BaseHTTPMiddleware):
    """Honor the X-Ingress-Path header from a reverse proxy (e.g. Home Assistant ingress).

    HA ingress forwards the per-session prefix (e.g. /api/hassio_ingress/<token>)
    via this header. We stash it on ``request.state.ingress_path`` so that
    redirects (``RedirectResponse(f"{prefix}/login")``) and the ``base_path``
    value passed to templates all produce URLs the upstream proxy can resolve.

    NOTE: We deliberately do NOT set ``scope["root_path"]`` — Starlette's
    Mount routing slices the request path by ``len(root_path)`` when resolving
    sub-apps (``StaticFiles``, sub-routers), which mangles paths that don't
    start with the prefix. The proxy already sends us unprefixed paths, so
    routing must stay prefix-unaware; only response URLs need the prefix.

    Without the header, ``ingress_path`` stays empty and the app behaves
    exactly as it did before — preserving direct-port and Kubernetes deployments.
    """

    async def dispatch(self, request: Request, call_next):
        ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
        request.state.ingress_path = ingress_path
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in OPEN_PATHS):
            return await call_next(request)
        # Browser-push bookmarklet: form POST with token auth (not cookie)
        if "/browser-push" in path:
            return await call_next(request)
        if is_authenticated(request):
            return await call_next(request)
        # API requests get 401, browser requests get redirect
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"{_root_path(request)}/login", status_code=303)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dude, Where's My Package?",
        version=pkg_version("dude-wheres-my-package"),
        lifespan=lifespan,
    )

    # Order matters: Starlette runs middleware in reverse of add_middleware() calls,
    # so IngressPathMiddleware (added last) runs first and AuthMiddleware sees the
    # ingress-aware root_path.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(IngressPathMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    @app.exception_handler(_LoginRequired)
    async def login_redirect(request: Request, exc: _LoginRequired):
        return RedirectResponse(f"{_root_path(request)}/login", status_code=303)

    static_dir = Path(__file__).parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(router)
    app.include_router(views_router)
    return app


app = create_app()
