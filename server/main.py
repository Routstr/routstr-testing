"""FastAPI entrypoint for the routstr-testing UI backend."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from runner.models import get_engine

from .config import ServerConfig
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


def create_app(
    config: Optional[ServerConfig] = None,
    orchestrate_runner: Optional[Callable] = None,
) -> FastAPI:
    config = config or ServerConfig.from_env()
    app = FastAPI(title="routstr-testing", version="0.1.0")
    app.state.config = config
    app.state.engine = get_engine(config.db_path)
    app.state.orchestrate_runner = orchestrate_runner or spawn_orchestrator

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(scenarios_router)
    app.include_router(runs_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    _install_redaction()
    return app


app = create_app()
