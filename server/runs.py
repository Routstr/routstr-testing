"""Run history + log endpoints, plus the POST /api/runs trigger.

Subprocess invocation note: the cashu token is passed to orchestrate.py
through E2E_CASHU_TOKEN (env), never argv (visible in `ps`) and never
written to the database or stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc
from sqlmodel import select

from runner.models import Run, TestResult, get_engine, get_session

from .schemas import (
    LogListing,
    RunCreate,
    RunCreated,
    RunDetail,
    RunSummary,
    TestResultOut,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _engine(request: Request):
    eng = getattr(request.app.state, "engine", None)
    if eng is None:
        eng = get_engine(request.app.state.config.db_path)
        request.app.state.engine = eng
    return eng


def _orchestrate_runner(request: Request):
    """Return the callable used to spawn the orchestrator.

    Tests override this to avoid actually launching a subprocess; default
    spawns `python -m runner.orchestrate` (configurable).
    """
    return request.app.state.orchestrate_runner


def _decode_remote_urls(raw: Optional[str]) -> Optional[list[str]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return None


def _to_summary(row: Run) -> RunSummary:
    return RunSummary(
        id=row.id or 0,
        scenario_id=row.scenario_id,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        token_consumed_sats=row.token_consumed_sats,
        token_consumed_msats=getattr(row, "token_consumed_msats", 0) or 0,
        target_profile=row.target_profile or "local",
        remote_node_urls=_decode_remote_urls(row.remote_node_urls_json),
        upstream_profile=row.upstream_profile or "mock",
        upstream_estimated_cost_usd=row.upstream_estimated_cost_usd,
        upstream_actual_cost_usd=row.upstream_actual_cost_usd,
    )


def _to_detail(row: Run, test_rows: list[TestResult]) -> RunDetail:
    try:
        commits = json.loads(row.vendor_commits_json) if row.vendor_commits_json else {}
    except json.JSONDecodeError:
        commits = {}
    return RunDetail(
        id=row.id or 0,
        scenario_id=row.scenario_id,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        token_consumed_sats=row.token_consumed_sats,
        token_consumed_msats=getattr(row, "token_consumed_msats", 0) or 0,
        target_profile=row.target_profile or "local",
        remote_node_urls=_decode_remote_urls(row.remote_node_urls_json),
        upstream_profile=row.upstream_profile or "mock",
        upstream_estimated_cost_usd=row.upstream_estimated_cost_usd,
        upstream_actual_cost_usd=row.upstream_actual_cost_usd,
        artifacts_dir=row.artifacts_dir,
        vendor_commits=commits,
        error_message=row.error_message,
        test_results=[
            TestResultOut(
                id=t.id or 0,
                test_name=t.test_name,
                outcome=t.outcome,
                duration_ms=t.duration_ms,
                error_excerpt=t.error_excerpt,
            )
            for t in test_rows
        ],
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    request: Request,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    scenario_id: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    target_profile: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RunSummary]:
    engine = _engine(request)
    with get_session(engine) as session:
        stmt = select(Run)
        if status_filter:
            stmt = stmt.where(Run.status == status_filter)
        if scenario_id:
            stmt = stmt.where(Run.scenario_id == scenario_id)
        if since:
            stmt = stmt.where(Run.started_at >= since)
        if target_profile:
            stmt = stmt.where(Run.target_profile == target_profile)
        stmt = stmt.order_by(desc(Run.id)).offset(offset).limit(limit)
        rows = session.exec(stmt).all()
    return [_to_summary(r) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: int, request: Request) -> RunDetail:
    engine = _engine(request)
    with get_session(engine) as session:
        row = session.get(Run, run_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"run {run_id} not found"
            )
        results = session.exec(
            select(TestResult).where(TestResult.run_id == run_id).order_by(TestResult.id)
        ).all()
    return _to_detail(row, list(results))


@router.post("", response_model=RunCreated, status_code=status.HTTP_201_CREATED)
def create_run(body: RunCreate, request: Request) -> RunCreated:
    config = request.app.state.config
    scenario_path = config.scenarios_dir / f"{body.scenario_id}.yaml"
    if not scenario_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"scenario {body.scenario_id!r} not found",
        )

    target_profile = (body.target_profile or "").strip().lower() or None
    if target_profile and target_profile not in {"local", "remote"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown target_profile {body.target_profile!r}; expected local|remote",
        )
    remote_urls = [u.strip() for u in (body.remote_node_urls or []) if u.strip()]
    if target_profile == "remote" and not remote_urls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "target_profile=remote requires at least one remote_node_urls "
                "entry — refusing to fall back to the local compose stack."
            ),
        )
    remote_admin_tokens = list(body.remote_admin_tokens or [])

    upstream_profile = (body.upstream_profile or "").strip().lower() or None
    # upstream_env keys are write-only; we forward them to the orchestrator as
    # env vars and never persist or echo them. Drop empty values so a blank
    # textarea field doesn't shadow a real key already in the server env.
    upstream_env = {
        k: v for k, v in (body.upstream_env or {}).items() if k and v
    } or None

    runner_fn = _orchestrate_runner(request)
    try:
        run_id = runner_fn(
            scenario_id=body.scenario_id,
            token=body.cashu_token,
            config=config,
            target_profile=target_profile,
            remote_node_urls=remote_urls or None,
            remote_admin_tokens=remote_admin_tokens or None,
            upstream_profile=upstream_profile,
            upstream_env=upstream_env,
            upstream_max_usd=body.upstream_max_usd,
        )
    except OrchestratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return RunCreated(run_id=run_id, scenario_id=body.scenario_id)


@router.get("/{run_id}/logs", response_model=LogListing)
def list_logs(run_id: int, request: Request) -> LogListing:
    engine = _engine(request)
    with get_session(engine) as session:
        row = session.get(Run, run_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"run {run_id} not found"
            )
    artifacts_dir = Path(row.artifacts_dir) if row.artifacts_dir else None
    files: list[str] = []
    if artifacts_dir and artifacts_dir.exists():
        files = sorted(p.name for p in artifacts_dir.iterdir() if p.is_file())
    return LogListing(
        run_id=run_id,
        artifacts_dir=str(artifacts_dir) if artifacts_dir else None,
        files=files,
    )


@router.get("/{run_id}/logs/{name}", response_class=PlainTextResponse)
def get_log(run_id: int, name: str, request: Request) -> PlainTextResponse:
    if "/" in name or "\\" in name or name.startswith(".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid log name"
        )
    engine = _engine(request)
    with get_session(engine) as session:
        row = session.get(Run, run_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"run {run_id} not found"
            )
    if not row.artifacts_dir:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no artifacts directory"
        )
    base = Path(row.artifacts_dir).resolve()
    target = (base / name).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid log path"
        ) from exc
    if not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"log {name!r} not found for run {run_id}",
        )
    return PlainTextResponse(target.read_text(errors="replace"))


# ---- subprocess driver --------------------------------------------------


class OrchestratorError(RuntimeError):
    """Raised when the orchestrator subprocess fails to spawn or report a run id."""


def spawn_orchestrator(
    *,
    scenario_id: str,
    token: str,
    config,
    target_profile: Optional[str] = None,
    remote_node_urls: Optional[list[str]] = None,
    remote_admin_tokens: Optional[list[str]] = None,
    upstream_profile: Optional[str] = None,
    upstream_env: Optional[dict[str, str]] = None,
    upstream_max_usd: Optional[float] = None,
) -> int:
    """Run the orchestrator and return the run id it inserted.

    The cashu token is passed via E2E_CASHU_TOKEN, never argv. Per-node
    admin tokens (ROU-151) are passed via REMOTE_NODE_ADMIN_TOKEN_<i> env
    vars — never argv, never persisted to runs.db. The remote node URLs
    DO go on argv since they're not secret (they end up in the runs.db
    row so the UI can show what was tested).

    ROU-153: the upstream provider key(s) in `upstream_env` are passed via the
    subprocess env (never argv, never persisted) — same secret contract as the
    cashu token. The upstream profile id is NOT secret and goes on argv so it
    can be recorded in the runs row.
    """
    cmd = list(config.orchestrate_cmd) + [
        "--scenario",
        scenario_id,
        "--db",
        str(config.db_path),
        "--scenarios-dir",
        str(config.scenarios_dir),
        "--compose-file",
        str(config.compose_file),
    ]
    if target_profile:
        cmd += ["--target-profile", target_profile]
    if remote_node_urls:
        cmd += ["--remote-node-urls", ",".join(remote_node_urls)]
    if upstream_profile:
        cmd += ["--upstream-profile", upstream_profile]
    if upstream_max_usd is not None:
        cmd += ["--upstream-max-usd", str(upstream_max_usd)]

    env = os.environ.copy()
    env["E2E_CASHU_TOKEN"] = token
    for idx, admin_token in enumerate(remote_admin_tokens or []):
        env[f"REMOTE_NODE_ADMIN_TOKEN_{idx}"] = admin_token
    for key, value in (upstream_env or {}).items():
        env[key] = value

    proc = subprocess.run(  # noqa: S603 — args fully controlled
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=int(env.get("SERVER_ORCHESTRATE_TIMEOUT", "1800")),
    )
    if proc.returncode != 0:
        raise OrchestratorError(
            f"orchestrator exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )
    return _parse_run_id(proc.stdout)


def _parse_run_id(stdout: str) -> int:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "run_id" in payload:
            return int(payload["run_id"])
    raise OrchestratorError("orchestrator produced no run_id summary line")
