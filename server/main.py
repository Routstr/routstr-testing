"""FastAPI entrypoint for the routstr-testing UI backend."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from runner.models import get_engine

from .balance import router as balance_router
from .config import REPO_ROOT, ServerConfig
from .providers import router as providers_router
from .runs import router as runs_router
from .runs import spawn_orchestrator
from .scenarios import router as scenarios_router

_TOKEN_REDACTION = "<redacted-cashu>"


class _TokenRedactionFilter(logging.Filter):
    """Last-line defense: scrub anything that looks like a cashu token from
    any log record before it leaves the server process. We pass the token
    via env to the orchestrator so it should never appear here — but if it
    ever does, this drops it on the floor.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "cashu" in msg.lower():
            record.msg = _TOKEN_REDACTION
            record.args = None
        return True


def _install_redaction() -> None:
    flt = _TokenRedactionFilter()
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "server"):
        logging.getLogger(name).addFilter(flt)
    logging.getLogger().addFilter(flt)


def _mount_webui(app: FastAPI, dist_dir: Path) -> bool:
    """Serve the built React UI from `dist_dir` on the same origin as the API.

    Mounting the SPA here means one process — and therefore one ngrok tunnel
    or one published host — serves both the UI and the `/api/*` backend, so
    `VITE_API_BASE_URL` can stay empty (same-origin fetch). No-op when the
    build output is absent (e.g. server-only test runs), so this never breaks
    the existing host-run dev setup.
    """
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        logging.getLogger("server").info(
            "webui dist not found at %s — UI not served (API only)", dist_dir
        )
        return False

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    def _spa(full_path: str, request: StarletteRequest) -> StarletteResponse:
        # API routes are registered before this catch-all, so they win. Serve a
        # real file when one exists (favicon etc.), else fall back to index.html
        # so client-side routes (/runs, /scenarios/...) deep-link correctly.
        if full_path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        candidate = (dist_dir / full_path).resolve()
        if (
            full_path
            and dist_dir.resolve() in candidate.parents
            and candidate.is_file()
        ):
            return FileResponse(str(candidate))
        return FileResponse(str(index_file))

    logging.getLogger("server").info("serving webui from %s", dist_dir)
    return True


def create_app(
    config: Optional[ServerConfig] = None,
    orchestrate_runner: Optional[Callable] = None,
    balance_fetcher: Optional[Callable] = None,
) -> FastAPI:
    config = config or ServerConfig.from_env()
    app = FastAPI(title="routstr-testing", version="0.1.0")
    app.state.config = config
    app.state.engine = get_engine(config.db_path)
    app.state.orchestrate_runner = orchestrate_runner or spawn_orchestrator
    app.state.balance_fetcher = balance_fetcher

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(scenarios_router)
    app.include_router(runs_router)
    app.include_router(balance_router)
    app.include_router(providers_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Serve the built SPA last so the /api/* routers above take precedence over
    # the catch-all. WEBUI_DIST_DIR overrides the default repo-root/webui/dist.
    webui_dist = Path(
        os.environ.get("WEBUI_DIST_DIR", REPO_ROOT / "webui" / "dist")
    )
    _mount_webui(app, webui_dist)

    _install_redaction()
    return app


app = create_app()
