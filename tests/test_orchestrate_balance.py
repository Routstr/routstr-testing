"""Orchestrator balance-diff telemetry (ROU-138).

We monkeypatch the routstrd balance fetcher so the test doesn't need a
real daemon, then verify the orchestrator writes the correct
`token_consumed_sats` to the runs row.

The smoke scenario is `services_required: false`, which intentionally
skips balance capture (no daemon to talk to). So this suite uses a
purpose-built scenario fixture with `services_required: true`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from runner import orchestrate as orch_mod
from runner.models import Run, get_engine, get_session


SCENARIO_YAML = """\
id: balance_smoke
name: Balance Smoke
description: Trivially passing scenario used to exercise the balance diff.
services_required: true
selection:
  paths: [tests/test_smoke.py]
  markers: []
parameters: {}
expected_cost_sats: 0
timeout_seconds: 60
"""


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch):
    """A scenarios/ dir + db path + bypassed sync/compose/topup wiring.

    The orchestrator usually drives docker compose; here we short-circuit
    every external step so we're testing one thing: balance-before minus
    balance-after lands in the runs row.
    """
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "balance_smoke.yaml").write_text(SCENARIO_YAML)

    # Skip sync.
    monkeypatch.setenv("SKIP_SYNC", "1")

    # Bypass docker so services_required=True doesn't try to actually start it.
    monkeypatch.setattr(orch_mod.shutil, "which", lambda _: None)

    # Topup script doesn't exist in the temp tree; the orchestrator's
    # topup helper handles that path. We don't need a real token.

    # Run pytest as a no-op that exits 0 with a junit-like file the parser
    # can ingest as zero results.
    junit_xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="t" tests="0" failures="0" errors="0">'
        '</testsuite></testsuites>'
    )

    def fake_run_pytest(scenario, junit_path, env):
        junit_path.write_text(junit_xml)
        return 0, "ok"

    monkeypatch.setattr(orch_mod, "_run_pytest", fake_run_pytest)

    return tmp_path, scenarios_dir


def _read_run(db_path: Path, run_id: int) -> Run:
    engine = get_engine(db_path)
    with get_session(engine) as session:
        row = session.get(Run, run_id)
        assert row is not None
        return row


def test_orchestrator_records_balance_diff(harness, monkeypatch):
    tmp_path, scenarios_dir = harness

    balances = iter([1500, 900])  # before, after → consumed 600

    def fake_fetch(*args, **kwargs):
        return next(balances)

    monkeypatch.setattr(orch_mod, "fetch_routstrd_total_sats", fake_fetch)

    db_path = tmp_path / "runs.db"
    run_id = orch_mod.orchestrate(
        scenario_id="balance_smoke",
        token="placeholder",
        db_path=db_path,
        scenarios_dir=scenarios_dir,
        compose_file=tmp_path / "compose.yml",
    )
    row = _read_run(db_path, run_id)
    assert row.token_consumed_sats == 600
    assert row.status == "passed"


def test_orchestrator_records_zero_when_fetch_unavailable(harness, monkeypatch):
    """If routstrd is unreachable both pre and post, consumed = 0, not crash."""
    tmp_path, scenarios_dir = harness
    monkeypatch.setattr(orch_mod, "fetch_routstrd_total_sats", lambda *a, **kw: None)

    db_path = tmp_path / "runs.db"
    run_id = orch_mod.orchestrate(
        scenario_id="balance_smoke",
        token="placeholder",
        db_path=db_path,
        scenarios_dir=scenarios_dir,
        compose_file=tmp_path / "compose.yml",
    )
    row = _read_run(db_path, run_id)
    assert row.token_consumed_sats == 0
    assert row.status == "passed"


def test_orchestrator_clamps_negative_diff_to_zero(harness, monkeypatch):
    """Refund mid-run (after > before) → don't report negative consumption."""
    tmp_path, scenarios_dir = harness
    balances = iter([500, 800])  # before < after → diff = -300, clamp to 0

    monkeypatch.setattr(
        orch_mod, "fetch_routstrd_total_sats", lambda *a, **kw: next(balances)
    )

    db_path = tmp_path / "runs.db"
    run_id = orch_mod.orchestrate(
        scenario_id="balance_smoke",
        token="placeholder",
        db_path=db_path,
        scenarios_dir=scenarios_dir,
        compose_file=tmp_path / "compose.yml",
    )
    row = _read_run(db_path, run_id)
    assert row.token_consumed_sats == 0


def test_orchestrator_skips_balance_capture_when_services_not_required(
    tmp_path: Path, monkeypatch
):
    """services_required=false (the canonical `smoke` scenario) must not
    attempt to talk to routstrd at all — the fetcher should never be called.
    """
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "no_services.yaml").write_text(
        "id: no_services\n"
        "name: No services\n"
        "services_required: false\n"
        "selection: {paths: [tests/test_smoke.py]}\n"
    )

    monkeypatch.setenv("SKIP_SYNC", "1")
    monkeypatch.setattr(orch_mod.shutil, "which", lambda _: None)

    call_count = {"n": 0}

    def fake_fetch(*a, **kw):
        call_count["n"] += 1
        return 100

    monkeypatch.setattr(orch_mod, "fetch_routstrd_total_sats", fake_fetch)

    junit_xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="t" tests="0" failures="0" errors="0">'
        '</testsuite></testsuites>'
    )

    def fake_run_pytest(scenario, junit_path, env):
        junit_path.write_text(junit_xml)
        return 0, "ok"

    monkeypatch.setattr(orch_mod, "_run_pytest", fake_run_pytest)

    db_path = tmp_path / "runs.db"
    run_id = orch_mod.orchestrate(
        scenario_id="no_services",
        token=None,
        db_path=db_path,
        scenarios_dir=scenarios_dir,
        compose_file=tmp_path / "compose.yml",
    )
    assert call_count["n"] == 0
    row = _read_run(db_path, run_id)
    assert row.token_consumed_sats == 0
