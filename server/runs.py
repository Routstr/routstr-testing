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


def _to_summary(row: Run) -> RunSummary:
    return RunSummary(
        id=row.id or 0,
        scenario_id=row.scenario_id,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        token_consumed_sats=row.token_consumed_sats,
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
    runner_fn = _orchestrate_runner(request)
    try:
        run_id = runner_fn(
            scenario_id=body.scenario_id,
            token=body.cashu_token,
            config=config,
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


def spawn_orchestrator(*, scenario_id: str, token: str, config) -> int:
    """Run the orchestrator and return the run id it inserted.

    The token is passed via E2E_CASHU_TOKEN, never argv. We capture stdout
    to read the `{run_id, db}` summary the orchestrator prints on exit;
    that capture is discarded after parsing so the token (if it ever leaked
    into output) does not survive in any handler.

    We forward --db / --scenarios-dir / --compose-file from the server
    config so the orchestrator writes the new run row to the same SQLite
    file the server reads from.
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
    env = os.environ.copy()
    env["E2E_CASHU_TOKEN"] = token
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
