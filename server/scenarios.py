"""Scenario CRUD against scenarios/*.yaml on disk.

The plan calls this the source of truth; the SQLite `scenarios` table is
only populated by the orchestrator after a run, so we read/write files
directly and never touch that table from the API.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status

from .schemas import ScenarioCreate, ScenarioDetail, ScenarioSummary, ScenarioUpdate

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_id(scenario_id: str) -> None:
    if not _ID_RE.match(scenario_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scenario id must match [A-Za-z0-9_-]+",
        )


def _scenarios_dir(request: Request) -> Path:
    return request.app.state.config.scenarios_dir


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
    return ScenarioDetail(
        id=scenario_id,
        name=str(data.get("name", scenario_id)),
        description=str(data.get("description", "")),
        yaml=raw,
        updated_at=None,
    )


router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("", response_model=list[ScenarioSummary])
def list_scenarios(scenarios_dir: Path = Depends(_scenarios_dir)) -> list[ScenarioSummary]:
    if not scenarios_dir.exists():
        return []
    out: list[ScenarioSummary] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        detail = _parse(scenarios_dir, path)
        out.append(
            ScenarioSummary(
                id=detail.id, name=detail.name, description=detail.description
            )
        )
    return out


@router.get("/{scenario_id}", response_model=ScenarioDetail)
def get_scenario(
    scenario_id: str, scenarios_dir: Path = Depends(_scenarios_dir)
) -> ScenarioDetail:
    _validate_id(scenario_id)
    path = _path_for(scenarios_dir, scenario_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"scenario {scenario_id!r} not found",
        )
    return _parse(scenarios_dir, path)


@router.post("", response_model=ScenarioDetail, status_code=status.HTTP_201_CREATED)
def create_scenario(
    body: ScenarioCreate, scenarios_dir: Path = Depends(_scenarios_dir)
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
    return _parse(scenarios_dir, path)


@router.put("/{scenario_id}", response_model=ScenarioDetail)
def update_scenario(
    scenario_id: str,
    body: ScenarioUpdate,
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
    return _parse(scenarios_dir, path)


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
