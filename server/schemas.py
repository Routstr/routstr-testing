"""Pydantic request/response models for the API.

These intentionally do NOT mirror the SQLModel rows verbatim — the API
shape is what the React UI will consume, and we want it stable even if
the persistence schema evolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ScenarioStats(BaseModel):
    runs_count: int = 0
    avg_consumed_sats: int = 0
    last_consumed_sats: Optional[int] = None


class ScenarioSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    expected_cost_sats: int = 0
    # ROU-153 — surfaced so the Run modal can show the USD cost preview and
    # the Scenarios list can flag real-upstream scenarios.
    upstream_profile: str = "mock"
    estimated_upstream_cost_usd: float = 0.0
    stats: ScenarioStats = Field(default_factory=ScenarioStats)


class ScenarioDetail(ScenarioSummary):
    yaml: str
    updated_at: Optional[datetime] = None


class BalanceEstimate(BaseModel):
    """Best-effort routstrd balance snapshot for the UI's run-modal warning.

    `total_sats` is None when routstrd is unreachable — the UI should
    surface "balance unknown" rather than block the user from running.
    """

    total_sats: Optional[int] = None
    source: str  # "routstrd" | "unavailable"
    fetched_at: datetime
    detail: Optional[str] = None


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
    # Precise spend in millisats (node billing is sub-sat, so the integer sats
    # field rounds tiny real spends to 0). The UI renders this.
    token_consumed_msats: int = 0
    # ROU-151 — `local` (default) or `remote`. Surfaced so the Runs table
    # can render a badge / filter without needing the detail endpoint.
    target_profile: str = "local"
    remote_node_urls: Optional[list[str]] = None
    # ROU-153 — upstream provider profile + USD cost telemetry.
    upstream_profile: str = "mock"
    upstream_estimated_cost_usd: Optional[float] = None
    upstream_actual_cost_usd: Optional[float] = None


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
    # ROU-151 target-profile fields. Admin tokens are write-only and never
    # persisted — same contract as `cashu_token`.
    target_profile: Optional[str] = Field(
        default=None,
        description="`local` (default) or `remote`. Overrides the scenario YAML.",
    )
    remote_node_urls: Optional[list[str]] = Field(
        default=None,
        description=(
            "Routstr node base URLs to point the harness at when "
            "target_profile=remote. At least one URL is required for remote."
        ),
    )
    remote_admin_tokens: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional per-node admin tokens, positional with remote_node_urls. "
            "Never persisted; passed to the orchestrator via "
            "REMOTE_NODE_ADMIN_TOKEN_<i> env vars."
        ),
    )
    # ROU-153 upstream-profile fields.
    upstream_profile: Optional[str] = Field(
        default=None,
        description="`mock` (default) or a providers/<id>.yaml id. Overrides the scenario YAML.",
    )
    upstream_env: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Per-provider env vars (e.g. {'OPENAI_API_KEY': 'sk-...'}). "
            "Write-only; forwarded to the orchestrator subprocess as env vars "
            "and NEVER persisted — same contract as cashu_token / admin tokens."
        ),
    )
    upstream_max_usd: Optional[float] = Field(
        default=None,
        description=(
            "Per-run override of the cost ceiling. The orchestrator refuses to "
            "start if the scenario's estimated upstream cost exceeds this."
        ),
    )


class RunCreated(BaseModel):
    run_id: int
    scenario_id: str


class ProviderRequiredEnv(BaseModel):
    name: str
    secret: bool = False
    has_default: bool = False


class ProviderModel(BaseModel):
    id: str
    name: str = ""


class ProviderSummary(BaseModel):
    """One upstream provider profile, for the UI Run-modal dropdown."""

    id: str
    name: str
    upstream_base_url: str
    api_key_env: str
    required_env: list[ProviderRequiredEnv] = []
    models: list[ProviderModel] = []
    notes: str = ""


class LogListing(BaseModel):
    run_id: int
    artifacts_dir: Optional[str]
    files: list[str]
