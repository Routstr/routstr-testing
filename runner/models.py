"""SQLModel schema for the orchestrator's runs database.

Tables follow the ROU-125 plan v4 persistence schema (plus the ROU-151
target-profile fields on `runs`):

  scenarios(id, name, description, yaml, updated_at)
  runs(id, scenario_id, started_at, finished_at, status, vendor_commits_json,
       token_consumed_sats, artifacts_dir, error_message,
       target_profile, remote_node_urls_json)
  test_results(id, run_id, test_name, outcome, duration_ms, error_excerpt)

`target_profile` and `remote_node_urls_json` are added by `get_engine()` via
an idempotent `ALTER TABLE ... ADD COLUMN` so existing databases pick the
new columns up on next startup without a separate migration step. Admin
tokens are NEVER persisted — they live only in the env var the orchestrator
passes to pytest.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import inspect, text
from sqlmodel import Field, Session, SQLModel, create_engine


class Scenario(SQLModel, table=True):
    __tablename__ = "scenarios"

    id: str = Field(primary_key=True)
    name: str
    description: Optional[str] = None
    yaml: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    status: str = Field(default="running")  # running|passed|failed|error
    vendor_commits_json: Optional[str] = None
    token_consumed_sats: int = 0
    artifacts_dir: Optional[str] = None
    error_message: Optional[str] = None
    # ROU-151: target-profile dimensions surfaced in the Runs UI.
    target_profile: str = Field(default="local", index=True)
    remote_node_urls_json: Optional[str] = None


class TestResult(SQLModel, table=True):
    __tablename__ = "test_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    test_name: str
    outcome: str  # passed|failed|skipped|error
    duration_ms: int = 0
    error_excerpt: Optional[str] = None
    log_path: Optional[str] = None


# Columns added after the initial `runs` table was created. Each entry is
# (column_name, "ALTER TABLE ... ADD COLUMN ..."). The ALTER runs only when
# the column is missing, so the orchestrator stays idempotent across the
# v3 → v4 schema bump (no Alembic required for this one-shot ADD).
_RUNS_LATER_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "target_profile",
        "ALTER TABLE runs ADD COLUMN target_profile TEXT NOT NULL DEFAULT 'local'",
    ),
    (
        "remote_node_urls_json",
        "ALTER TABLE runs ADD COLUMN remote_node_urls_json TEXT",
    ),
)


def _ensure_late_columns(engine) -> None:
    inspector = inspect(engine)
    if "runs" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("runs")}
    with engine.begin() as conn:
        for column, ddl in _RUNS_LATER_COLUMNS:
            if column not in existing:
                conn.execute(text(ddl))


def get_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(engine)
    _ensure_late_columns(engine)
    return engine


def get_session(engine) -> Session:
    return Session(engine)
