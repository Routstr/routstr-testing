"""Orchestrator process exit code must reflect the money-path outcome.

This is the CI-integrity guard. The no-docker `pr` lane wires a mock
money-path scenario through `python -m runner.orchestrate` and relies on
the process exit code to gate the PR. If the orchestrator exits 0 even
when the scenario's tests FAIL (status=failed) or ERROR (status=error),
the CI lane is a silent-pass: it stays green while the money logic is
broken — the exact false-safety failure mode flagged on the foreign-mint
swap PR.

These tests drive `runner.orchestrate.main(argv)` (the module entry point
CI calls) end-to-end with sync/compose/topup short-circuited, so they need
no Docker and no funds, and assert the returned exit code maps:

    passed  -> 0
    failed  -> non-zero
    error   -> non-zero
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runner import orchestrate as orch_mod

PASS_SCENARIO = """\
id: exit_pass
name: Exit Pass
description: Mock money-path scenario whose tests pass.
services_required: false
selection:
  paths: [tests/test_smoke.py]
  markers: []
parameters: {}
expected_cost_sats: 0
timeout_seconds: 60
"""

FAIL_SCENARIO = """\
id: exit_fail
name: Exit Fail
description: Mock money-path scenario whose tests fail (status=failed).
services_required: false
selection:
  paths: [tests/test_smoke_fail.py]
  markers: []
parameters: {}
expected_cost_sats: 0
timeout_seconds: 60
"""


def _junit(failures: int) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="t" tests="1" failures="{failures}" '
        'errors="0">'
        '<testcase classname="c" name="t" time="0.01">'
        + ('<failure message="boom">boom</failure>' if failures else '')
        + '</testcase></testsuite></testsuites>'
    )


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch):
    """scenarios/ dir + bypassed sync/compose/topup, mockable pytest rc."""
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "exit_pass.yaml").write_text(PASS_SCENARIO)
    (scenarios_dir / "exit_fail.yaml").write_text(FAIL_SCENARIO)

    monkeypatch.setenv("SKIP_SYNC", "1")
    # No docker -> services_required paths are inert, but these scenarios
    # are services_required: false anyway.
    monkeypatch.setattr(orch_mod.shutil, "which", lambda _: None)

    db_path = tmp_path / "runs.db"
    monkeypatch.setattr(orch_mod, "DEFAULT_DB", db_path)
    monkeypatch.setattr(orch_mod, "DEFAULT_SCENARIOS", scenarios_dir)

    return tmp_path, scenarios_dir, db_path


def _run_main(monkeypatch, scenarios_dir: Path, db_path: Path, scenario: str,
              rc: int) -> int:
    """Drive main() with pytest stubbed to return `rc` + a matching junit."""

    def fake_run_pytest(scenario_obj, junit_path, env):
        junit_path.write_text(_junit(failures=1 if rc else 0))
        return rc, "stubbed pytest output"

    monkeypatch.setattr(orch_mod, "_run_pytest", fake_run_pytest)
    return orch_mod.main(
        [
            "--scenario",
            scenario,
            "--token",
            "placeholder",
            "--db",
            str(db_path),
            "--scenarios-dir",
            str(scenarios_dir),
        ]
    )


def test_passing_scenario_exits_zero(harness, monkeypatch):
    _, scenarios_dir, db_path = harness
    code = _run_main(monkeypatch, scenarios_dir, db_path, "exit_pass", rc=0)
    assert code == 0


def test_failing_scenario_exits_nonzero(harness, monkeypatch):
    """A money-path scenario whose tests FAIL must not exit 0.

    Regression guard: previously main() returned 0 regardless of the
    scenario outcome, so a CI money-path lane stayed green while the
    swap/refund logic under test was red.
    """
    _, scenarios_dir, db_path = harness
    code = _run_main(monkeypatch, scenarios_dir, db_path, "exit_fail", rc=1)
    assert code != 0, (
        "orchestrator exited 0 for a FAILED money-path scenario — the CI "
        "guard would silently pass while the money logic is broken"
    )


def test_errored_scenario_exits_nonzero(harness, monkeypatch):
    """no-tests-collected (pytest rc=5) -> status=error -> non-zero exit."""
    _, scenarios_dir, db_path = harness
    code = _run_main(monkeypatch, scenarios_dir, db_path, "exit_pass", rc=5)
    assert code != 0
