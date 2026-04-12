import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dude, Where's My Package?",
        version="0.1.0",
        lifespan=lifespan,
    )

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
