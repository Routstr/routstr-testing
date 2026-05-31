"""Server runtime configuration.

Locations are resolved relative to the repo root by default but every
path can be overridden via environment or via FastAPI app state, so the
test suite can point the server at a temporary directory without
touching the on-disk scenarios/runs.db.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ServerConfig:
    scenarios_dir: Path
    db_path: Path
    logs_dir: Path
    compose_file: Path
    orchestrate_cmd: list[str]
    cors_origins: list[str]
    routstrd_url: str = "http://localhost:8091"
    providers_dir: Path = REPO_ROOT / "providers"

    @classmethod
    def from_env(cls) -> "ServerConfig":
        return cls(
            scenarios_dir=Path(
                os.environ.get("SERVER_SCENARIOS_DIR", REPO_ROOT / "scenarios")
            ),
            providers_dir=Path(
                os.environ.get("SERVER_PROVIDERS_DIR", REPO_ROOT / "providers")
            ),
            db_path=Path(os.environ.get("SERVER_DB_PATH", REPO_ROOT / "runs.db")),
            logs_dir=Path(os.environ.get("SERVER_LOGS_DIR", REPO_ROOT / "logs")),
            compose_file=Path(
                os.environ.get("SERVER_COMPOSE_FILE", REPO_ROOT / "compose.yml")
            ),
            orchestrate_cmd=os.environ.get(
                "SERVER_ORCHESTRATE_CMD", "python -m runner.orchestrate"
            ).split(),
            cors_origins=[
                o.strip()
                for o in os.environ.get(
                    "SERVER_CORS_ORIGINS", "http://localhost:5173"
                ).split(",")
                if o.strip()
            ],
            routstrd_url=os.environ.get(
                "ROUTSTRD_URL", "http://localhost:8091"
            ).rstrip("/"),
        )
