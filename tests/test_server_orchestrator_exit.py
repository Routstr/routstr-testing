"""Server tolerance of the orchestrator's new non-zero exit on failed runs.

The orchestrator now exits non-zero when a scenario's tests fail/error so
CLI/CI can gate on the process exit code. For the FastAPI UI that is still a
*recorded* run (the run row exists with status=failed), so `spawn_orchestrator`
must return the run_id instead of raising — as long as a run_id summary line
was emitted. A non-zero exit with NO run_id line (e.g. a config error before
any run was recorded) must still raise OrchestratorError.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from server.runs import OrchestratorError, spawn_orchestrator


def _cfg(tmp_path: Path, orchestrate_cmd: list[str]):
    class Cfg:
        db_path = tmp_path / "runs.db"
        scenarios_dir = tmp_path / "scenarios"
        logs_dir = tmp_path / "logs"
        compose_file = tmp_path / "compose.yml"
        cors_origins = ["http://localhost:5173"]

    Cfg.orchestrate_cmd = orchestrate_cmd
    return Cfg


def _fake_orchestrator(tmp_path: Path, *, body: str) -> list[str]:
    script = tmp_path / "fake_orchestrate.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


def test_nonzero_exit_with_run_id_returns_run_id(tmp_path: Path):
    """A failed scenario (run recorded, exit 1) → return the run_id, not raise."""
    cmd = _fake_orchestrator(
        tmp_path,
        body="""
        import json, sys
        print(json.dumps({'run_id': 7, 'db': 'x', 'status': 'failed'}))
        sys.exit(1)
        """,
    )
    run_id = spawn_orchestrator(
        scenario_id="smoke", token="cashuX", config=_cfg(tmp_path, cmd)
    )
    assert run_id == 7


def test_nonzero_exit_without_run_id_raises(tmp_path: Path):
    """A true orchestrator failure (no run recorded) must still raise."""
    cmd = _fake_orchestrator(
        tmp_path,
        body="""
        import sys
        sys.stderr.write('upstream config error: boom')
        sys.exit(2)
        """,
    )
    with pytest.raises(OrchestratorError):
        spawn_orchestrator(
            scenario_id="smoke", token="cashuX", config=_cfg(tmp_path, cmd)
        )


def test_zero_exit_with_run_id_returns_run_id(tmp_path: Path):
    """The unchanged happy path still works."""
    cmd = _fake_orchestrator(
        tmp_path,
        body="""
        import json
        print(json.dumps({'run_id': 3, 'db': 'x', 'status': 'passed'}))
        """,
    )
    run_id = spawn_orchestrator(
        scenario_id="smoke", token="cashuX", config=_cfg(tmp_path, cmd)
    )
    assert run_id == 3
