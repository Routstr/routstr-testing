"""Scenario-driven orchestrator for the Routstr test harness.

Acceptance from ROU-133:
  python runner/orchestrate.py --scenario smoke --token <cashu>
  → produces a `runs` row in runs.db with linked `test_results`,
    and captures resolved vendor shas in `runs.vendor_commits_json`.

Pipeline:
  1. scripts/sync.sh (unless SKIP_SYNC=1)
  2. resolve scenario YAML → pytest args + env
  3. compose up + wait_for + topup routstrd (best-effort; missing scripts
     yield warnings, not crashes — the orchestrator's job is the pipeline)
  4. pytest with --junitxml
  5. parse junit, write rows to runs.db
  6. on failure, dump per-service logs into logs/<service>.log
  7. compose down (unless KEEP_UP=1)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import select

from .compose import dump_logs as compose_dump_logs
from .compose import down as compose_down
from .compose import up as compose_up
from .junit import parse_junit
from .models import Run, Scenario as ScenarioRow, TestResult, get_engine, get_session
from .scenario import Scenario, load_scenario

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "runs.db"
DEFAULT_LOGS = REPO_ROOT / "logs"
DEFAULT_COMPOSE = REPO_ROOT / "compose.yml"
DEFAULT_SCENARIOS = REPO_ROOT / "scenarios"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync.sh"
TOPUP_SCRIPT = REPO_ROOT / "scripts" / "topup_routstrd.sh"
WAIT_FOR_SCRIPT = REPO_ROOT / "scripts" / "wait_for.sh"
COMMITS_FILE = REPO_ROOT / "vendor" / "COMMITS.txt"


def _log(msg: str) -> None:
    print(f"[orchestrate] {msg}", flush=True)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _run_sync() -> tuple[bool, str]:
    if _env_flag("SKIP_SYNC"):
        _log("SKIP_SYNC=1 → skipping scripts/sync.sh")
        return True, ""
    if not SYNC_SCRIPT.exists():
        return False, f"sync script missing: {SYNC_SCRIPT}"
    proc = subprocess.run(
        ["bash", str(SYNC_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _read_vendor_commits() -> dict[str, str]:
    if not COMMITS_FILE.exists():
        return {}
    commits: dict[str, str] = {}
    for line in COMMITS_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            commits[parts[0]] = parts[1]
    return commits


def _wait_for_services() -> tuple[bool, str]:
    """Optional readiness wait — runs scripts/wait_for.sh if present."""
    if not WAIT_FOR_SCRIPT.exists():
        return True, "wait_for.sh not present; skipping readiness probe"
    proc = subprocess.run(
        ["bash", str(WAIT_FOR_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _topup(token: str) -> tuple[bool, str]:
    """Topup routstrd with the provided cashu token."""
    if not TOPUP_SCRIPT.exists():
        return False, (
            f"topup script missing: {TOPUP_SCRIPT}. Skipped topup; tests "
            "requiring funded_daemon will be flagged."
        )
    proc = subprocess.run(
        ["bash", str(TOPUP_SCRIPT), token],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _build_pytest_cmd(
    scenario: Scenario, junit_path: Path
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        f"--junitxml={junit_path}",
    ]
    cmd += scenario.selection.to_pytest_args()
    return cmd


def _run_pytest(
    scenario: Scenario, junit_path: Path, scenario_env: dict[str, str]
) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(scenario_env)
    cmd = _build_pytest_cmd(scenario, junit_path)
    _log("pytest: " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=scenario.timeout_seconds,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _ensure_scenario_row(engine, scenario: Scenario) -> None:
    with get_session(engine) as session:
        existing = session.exec(
            select(ScenarioRow).where(ScenarioRow.id == scenario.id)
        ).first()
        if existing is None:
            session.add(
                ScenarioRow(
                    id=scenario.id,
                    name=scenario.name,
                    description=scenario.description,
                    yaml=scenario.raw_yaml,
                    updated_at=datetime.utcnow(),
                )
            )
        else:
            existing.name = scenario.name
            existing.description = scenario.description
            existing.yaml = scenario.raw_yaml
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        session.commit()


def _insert_run(engine, *, scenario_id: str, artifacts_dir: Path) -> int:
    with get_session(engine) as session:
        row = Run(
            scenario_id=scenario_id,
            started_at=datetime.utcnow(),
            status="running",
            artifacts_dir=str(artifacts_dir),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None
        return row.id


def _finalize_run(
    engine,
    *,
    run_id: int,
    status: str,
    vendor_commits: dict[str, str],
    error_message: str | None = None,
    token_consumed_sats: int = 0,
) -> None:
    with get_session(engine) as session:
        row = session.get(Run, run_id)
        if row is None:
            return
        row.status = status
        row.finished_at = datetime.utcnow()
        row.vendor_commits_json = json.dumps(vendor_commits)
        row.error_message = error_message
        row.token_consumed_sats = token_consumed_sats
        session.add(row)
        session.commit()


def _insert_test_results(engine, run_id: int, parsed) -> None:
    if not parsed:
        return
    with get_session(engine) as session:
        for p in parsed:
            session.add(
                TestResult(
                    run_id=run_id,
                    test_name=p.test_name,
                    outcome=p.outcome,
                    duration_ms=p.duration_ms,
                    error_excerpt=p.error_excerpt,
                )
            )
        session.commit()


def _new_artifacts_dir() -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = DEFAULT_LOGS / stamp
    target.mkdir(parents=True, exist_ok=True)
    return target


def orchestrate(
    *,
    scenario_id: str,
    token: str | None,
    db_path: Path = DEFAULT_DB,
    scenarios_dir: Path = DEFAULT_SCENARIOS,
    compose_file: Path = DEFAULT_COMPOSE,
) -> int:
    """Run the orchestrator for one scenario.

    Returns the run id (rowid) inserted into the runs table.
    """
    engine = get_engine(db_path)
    scenario = load_scenario(scenarios_dir, scenario_id)
    _ensure_scenario_row(engine, scenario)

    artifacts_dir = _new_artifacts_dir()
    run_id = _insert_run(
        engine, scenario_id=scenario.id, artifacts_dir=artifacts_dir
    )
    _log(f"run #{run_id} → artifacts at {artifacts_dir}")

    overall_status = "passed"
    error_message: str | None = None
    teardown_logs_needed = False

    try:
        ok, output = _run_sync()
        (artifacts_dir / "sync.log").write_text(output or "")
        if not ok:
            overall_status = "error"
            error_message = "sync.sh failed"
            return run_id

        compose_used = (
            scenario.services_required
            and compose_file.exists()
            and shutil.which("docker") is not None
        )
        if compose_used:
            up_ok, up_out = compose_up(compose_file, project_dir=REPO_ROOT)
            (artifacts_dir / "compose-up.log").write_text(up_out)
            if not up_ok:
                overall_status = "error"
                error_message = "docker compose up failed"
                teardown_logs_needed = True
                return run_id

            wait_ok, wait_out = _wait_for_services()
            (artifacts_dir / "wait_for.log").write_text(wait_out)
            if not wait_ok:
                overall_status = "error"
                error_message = "readiness probe failed"
                teardown_logs_needed = True
                return run_id
        else:
            _log(
                "compose bring-up skipped "
                f"(services_required={scenario.services_required})"
            )

        token_ok = True
        if token and scenario.services_required:
            topup_ok, topup_out = _topup(token)
            (artifacts_dir / "topup.log").write_text(topup_out)
            if not topup_ok:
                token_ok = False
                _log("topup failed — continuing; funded tests will be flagged")

        junit_path = artifacts_dir / "junit.xml"
        scenario_env = scenario.env()
        if not token_ok:
            scenario_env["TOPUP_FAILED"] = "1"

        try:
            rc, output = _run_pytest(scenario, junit_path, scenario_env)
        except subprocess.TimeoutExpired as exc:
            (artifacts_dir / "pytest.log").write_text(
                f"timeout after {scenario.timeout_seconds}s\n{exc}"
            )
            overall_status = "error"
            error_message = f"pytest timed out after {scenario.timeout_seconds}s"
            teardown_logs_needed = True
            return run_id

        (artifacts_dir / "pytest.log").write_text(output)

        parsed = parse_junit(junit_path)
        _insert_test_results(engine, run_id, parsed)

        if rc == 5:  # pytest "no tests collected"
            overall_status = "error"
            error_message = "no tests collected for selection"
        elif rc != 0:
            overall_status = "failed"
            teardown_logs_needed = True

    except Exception as exc:  # safety net so we always finalize the row
        overall_status = "error"
        error_message = f"orchestrator crashed: {exc!r}"
        _log(error_message)
    finally:
        vendor_commits = _read_vendor_commits()

        compose_active = (
            scenario.services_required
            and compose_file.exists()
            and shutil.which("docker") is not None
        )
        if teardown_logs_needed and compose_active:
            try:
                compose_dump_logs(
                    compose_file,
                    project_dir=REPO_ROOT,
                    logs_dir=artifacts_dir,
                )
            except Exception as exc:  # don't let log dumping mask the failure
                _log(f"log dump failed: {exc!r}")

        if not _env_flag("KEEP_UP") and compose_active:
            try:
                compose_down(compose_file, project_dir=REPO_ROOT)
            except Exception as exc:
                _log(f"compose down failed: {exc!r}")

        _finalize_run(
            engine,
            run_id=run_id,
            status=overall_status,
            vendor_commits=vendor_commits,
            error_message=error_message,
        )
        _log(
            f"run #{run_id} status={overall_status} commits={list(vendor_commits)}"
        )

    return run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrate")
    parser.add_argument("--scenario", required=True, help="Scenario id (YAML stem)")
    parser.add_argument(
        "--token",
        default=os.environ.get("E2E_CASHU_TOKEN"),
        help="Cashu token used to topup routstrd (or set $E2E_CASHU_TOKEN)",
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help="Path to runs.db (SQLite)"
    )
    parser.add_argument(
        "--scenarios-dir",
        default=str(DEFAULT_SCENARIOS),
        help="Directory containing scenario YAML files",
    )
    parser.add_argument(
        "--compose-file",
        default=str(DEFAULT_COMPOSE),
        help="Path to docker compose file",
    )
    args = parser.parse_args(argv)

    run_id = orchestrate(
        scenario_id=args.scenario,
        token=args.token,
        db_path=Path(args.db),
        scenarios_dir=Path(args.scenarios_dir),
        compose_file=Path(args.compose_file),
    )

    # Echo a machine-friendly summary so callers (FastAPI server in ROU-134)
    # can pick up the new run id without re-querying SQLite.
    print(json.dumps({"run_id": run_id, "db": args.db}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
