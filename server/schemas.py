"""Pydantic request/response models for the API.

These intentionally do NOT mirror the SQLModel rows verbatim — the API
shape is what the React UI will consume, and we want it stable even if
the persistence schema evolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScenarioSummary(BaseModel):
    id: str
    name: str
    description: str = ""


class ScenarioDetail(ScenarioSummary):
    yaml: str
    updated_at: Optional[datetime] = None


class ScenarioCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    yaml: str


class ScenarioUpdate(BaseModel):
    yaml: str


class RunSummary(BaseModel):
    id: int
    scenario_id: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    token_consumed_sats: int = 0


class TestResultOut(BaseModel):
    id: int
    test_name: str
    outcome: str
    duration_ms: int
    error_excerpt: Optional[str] = None


class RunDetail(RunSummary):
    artifacts_dir: Optional[str] = None
    vendor_commits: dict[str, str] = {}
    error_message: Optional[str] = None
    test_results: list[TestResultOut] = []


class RunCreate(BaseModel):
    scenario_id: str
    cashu_token: str = Field(..., min_length=1)


class RunCreated(BaseModel):
    run_id: int
    scenario_id: str


class LogListing(BaseModel):
    run_id: int
    artifacts_dir: Optional[str]
    files: list[str]
