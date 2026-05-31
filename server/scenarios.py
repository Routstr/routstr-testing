"""Scenario CRUD against scenarios/*.yaml on disk.

The plan calls this the source of truth; the SQLite `scenarios` table is
only populated by the orchestrator after a run, so we read/write files
directly and never touch that table from the API.

For UI telemetry (ROU-138), each scenario also carries `expected_cost_sats`
(parsed from YAML) and a `stats` block (runs_count / avg_consumed_sats /
last_consumed_sats) joined against the runs table.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, func
from sqlmodel import select

from runner.models import Run, get_session

from .schemas import (
    ScenarioCreate,
    ScenarioDetail,
    ScenarioStats,
    ScenarioSummary,
    ScenarioUpdate,
)

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_id(scenario_id: str) -> None:
    if not _ID_RE.match(scenario_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scenario id must match [A-Za-z0-9_-]+",
        )


def _scenarios_dir(request: Request) -> Path:
    return request.app.state.config.scenarios_dir


def _engine(request: Request):
    return request.app.state.engine


def _path_for(scenarios_dir: Path, scenario_id: str) -> Path:
    return scenarios_dir / f"{scenario_id}.yaml"


def _parse(scenarios_dir: Path, path: Path) -> ScenarioDetail:
    raw = path.read_text()
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid yaml in {path.name}: {exc}",
        ) from exc
    scenario_id = data.get("id") or path.stem
    try:
        expected_cost_sats = int(data.get("expected_cost_sats", 0) or 0)
    except (TypeError, ValueError):
        expected_cost_sats = 0
    try:
        estimated_upstream_cost_usd = float(
            data.get("estimated_upstream_cost_usd", 0) or 0
        )
    except (TypeError, ValueError):
        estimated_upstream_cost_usd = 0.0
    return ScenarioDetail(
        id=scenario_id,
        name=str(data.get("name", scenario_id)),
        description=str(data.get("description", "")),
        expected_cost_sats=expected_cost_sats,
        upstream_profile=str(data.get("upstream_profile", "mock")).lower(),
        estimated_upstream_cost_usd=estimated_upstream_cost_usd,
        yaml=raw,
        updated_at=None,
    )


def _aggregate_stats(engine, scenario_ids: Iterable[str]) -> dict[str, ScenarioStats]:
    """Bulk-fetch per-scenario stats so list views don't N+1 the DB."""
    ids = list({s for s in scenario_ids if s})
    if not ids:
        return {}
    with get_session(engine) as session:
        stmt = (
            select(
                Run.scenario_id,
                func.count(Run.id),
                func.avg(Run.token_consumed_sats),
            )
            .where(Run.scenario_id.in_(ids))
            .group_by(Run.scenario_id)
        )
        agg = session.exec(stmt).all()
        last_consumed: dict[str, int] = {}
        for sid in ids:
            row = session.exec(
                select(Run.token_consumed_sats)
                .where(Run.scenario_id == sid)
                .where(Run.finished_at.is_not(None))
                .order_by(desc(Run.finished_at))
                .limit(1)
            ).first()
            if row is not None:
                last_consumed[sid] = int(row)
    out: dict[str, ScenarioStats] = {}
    for sid, count, avg in agg:
        out[sid] = ScenarioStats(
            runs_count=int(count or 0),
            avg_consumed_sats=int(round(float(avg or 0))),
            last_consumed_sats=last_consumed.get(sid),
        )
    for sid in ids:
        if sid not in out:
            out[sid] = ScenarioStats(
                runs_count=0,
                avg_consumed_sats=0,
                last_consumed_sats=last_consumed.get(sid),
            )
    return out


router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("", response_model=list[ScenarioSummary])
def list_scenarios(
    request: Request,
    scenarios_dir: Path = Depends(_scenarios_dir),
) -> list[ScenarioSummary]:
    if not scenarios_dir.exists():
        return []
    parsed = [_parse(scenarios_dir, p) for p in sorted(scenarios_dir.glob("*.yaml"))]
    stats = _aggregate_stats(_engine(request), [d.id for d in parsed])
    return [
        ScenarioSummary(
            id=d.id,
            name=d.name,
            description=d.description,
            expected_cost_sats=d.expected_cost_sats,
            upstream_profile=d.upstream_profile,
            estimated_upstream_cost_usd=d.estimated_upstream_cost_usd,
            stats=stats.get(d.id, ScenarioStats()),
        )
        for d in parsed
    ]


def _attach_stats(
    detail: ScenarioDetail, request: Request
) -> ScenarioDetail:
    stats = _aggregate_stats(_engine(request), [detail.id]).get(
        detail.id, ScenarioStats()
    )
    detail.stats = stats
    return detail


@router.get("/{scenario_id}", response_model=ScenarioDetail)
def get_scenario(
    scenario_id: str,
    request: Request,
    scenarios_dir: Path = Depends(_scenarios_dir),
) -> ScenarioDetail:
    _validate_id(scenario_id)
    path = _path_for(scenarios_dir, scenario_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"scenario {scenario_id!r} not found",
        )
    return _attach_stats(_parse(scenarios_dir, path), request)


@router.post("", response_model=ScenarioDetail, status_code=status.HTTP_201_CREATED)
def create_scenario(
    body: ScenarioCreate,
    request: Request,
    scenarios_dir: Path = Depends(_scenarios_dir),
) -> ScenarioDetail:
    _validate_id(body.id)
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    path = _path_for(scenarios_dir, body.id)
    if path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"scenario {body.id!r} already exists",
        )
    try:
        parsed = yaml.safe_load(body.yaml) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid yaml: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scenario YAML must be a mapping",
        )
    path.write_text(body.yaml)
    return _attach_stats(_parse(scenarios_dir, path), request)


@router.put("/{scenario_id}", response_model=ScenarioDetail)
def update_scenario(
    scenario_id: str,
    body: ScenarioUpdate,
    request: Request,
    scenarios_dir: Path = Depends(_scenarios_dir),
) -> ScenarioDetail:
    _validate_id(scenario_id)
    path = _path_for(scenarios_dir, scenario_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"scenario {scenario_id!r} not found",
        )
    try:
        parsed = yaml.safe_load(body.yaml) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid yaml: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scenario YAML must be a mapping",
        )
    path.write_text(body.yaml)
    return _attach_stats(_parse(scenarios_dir, path), request)


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(
    scenario_id: str, scenarios_dir: Path = Depends(_scenarios_dir)
) -> None:
    _validate_id(scenario_id)
    path = _path_for(scenarios_dir, scenario_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"scenario {scenario_id!r} not found",
        )
    path.unlink()
    return None
