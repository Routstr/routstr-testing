"""SQLModel schema for the orchestrator's runs database.

Tables follow the ROU-125 plan v4 persistence schema:
  scenarios(id, name, description, yaml, updated_at)
  runs(id, scenario_id, started_at, finished_at, status, vendor_commits_json,
       token_consumed_sats, artifacts_dir)
  test_results(id, run_id, test_name, outcome, duration_ms, error_excerpt)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

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


class TestResult(SQLModel, table=True):
    __tablename__ = "test_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    test_name: str
    outcome: str  # passed|failed|skipped|error
    duration_ms: int = 0
    error_excerpt: Optional[str] = None
    log_path: Optional[str] = None


def get_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


def get_session(engine) -> Session:
    return Session(engine)
