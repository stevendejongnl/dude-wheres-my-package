import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dwmp.api.auth import is_authenticated

from importlib.metadata import version as pkg_version

from dwmp.api.routes import router
from dwmp.api.views import router as views_router, _LoginRequired
from dwmp.api.dependencies import get_repository, get_tracking_service
from dwmp.services.scheduler import PackageScheduler


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

    yield

    scheduler.stop()
    await repo.close()


OPEN_PATHS = {"/health", "/login", "/static", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in OPEN_PATHS):
            return await call_next(request)
        if is_authenticated(request):
            return await call_next(request)
        # API requests get 401, browser requests get redirect
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse("/login", status_code=303)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dude, Where's My Package?",
        version=pkg_version("dude-wheres-my-package"),
        lifespan=lifespan,
    )

    app.add_middleware(AuthMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(_LoginRequired)
    async def login_redirect(request: Request, exc: _LoginRequired):
        return RedirectResponse("/login", status_code=303)

    static_dir = Path(__file__).parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(router)
    app.include_router(views_router)
    return app


app = create_app()
