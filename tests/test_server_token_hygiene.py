"""Token hygiene: the cashu token must never be persisted, logged, or
visible on argv. These tests exist because the spec calls token leakage
non-negotiable; they will be the first thing to break if anyone wires
the token into the runs table or a log handler.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runner.models import get_engine, get_session
from server.config import ServerConfig
from server.main import create_app
from server.runs import spawn_orchestrator


TOKEN = "cashuA-secret-do-not-leak-1234567890"


@pytest.fixture()
def config(tmp_path: Path) -> ServerConfig:
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "smoke.yaml").write_text("id: smoke\nname: Smoke\n")
    return ServerConfig(
        scenarios_dir=scenarios_dir,
        db_path=tmp_path / "runs.db",
        logs_dir=tmp_path / "logs",
        compose_file=tmp_path / "compose.yml",
        orchestrate_cmd=["true"],
        cors_origins=["http://localhost:5173"],
    )


def test_no_token_column_on_runs_table(config):
    """Hard schema check: there must be no column whose name suggests
    storage of the cashu token on the `runs` table.
    """
    engine = get_engine(config.db_path)
    with sqlite3.connect(config.db_path) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()]
    banned = {"cashu_token", "token", "cashutoken"}
    leaks = banned & {c.lower() for c in cols}
    assert not leaks, f"runs table has banned columns: {leaks}"


def test_token_not_persisted_after_run(config, capsys):
    """End-to-end: POST a run with a known token, then assert the token
    string never appears in the sqlite db, in the captured stdout, or in
    any test_results row.
    """

    def fake_runner(
        *,
        scenario_id,
        token,
        config,
        target_profile=None,
        remote_node_urls=None,
        remote_admin_tokens=None,
        upstream_profile=None,
        upstream_env=None,
        upstream_max_usd=None,
    ):
        # Simulate orchestrator inserting a row — without storing the token.
        from datetime import datetime

        from runner.models import Run

        engine = get_engine(config.db_path)
        with get_session(engine) as session:
            row = Run(
                scenario_id=scenario_id,
                started_at=datetime.utcnow(),
                status="passed",
                artifacts_dir=None,
                target_profile=target_profile or "local",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    app = create_app(config=config, orchestrate_runner=fake_runner)
    c = TestClient(app)
    r = c.post("/api/runs", json={"scenario_id": "smoke", "cashu_token": TOKEN})
    assert r.status_code == 201

    # Response body must not echo the token back
    assert TOKEN not in r.text

    # SQLite dump must not contain the token
    with sqlite3.connect(config.db_path) as conn:
        for table in ("runs", "test_results", "scenarios"):
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue
            assert TOKEN not in repr(rows), (
                f"token leaked into table {table}: {rows!r}"
            )

    # Captured stdout/stderr must not contain the token
    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err


def test_token_not_on_argv_when_spawning_orchestrator(tmp_path: Path):
    """Spawn a *real* subprocess that records its argv and env, then
    invoke spawn_orchestrator pointing at it. The token must be visible
    in the env (E2E_CASHU_TOKEN) but never on argv.
    """
    db_path = tmp_path / "runs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    capture = tmp_path / "argv_env.txt"
    fake_orchestrator = tmp_path / "fake_orchestrate.py"
    fake_orchestrator.write_text(
        textwrap.dedent(
            f"""
            import json, os, sys
            with open({str(capture)!r}, 'w') as fh:
                fh.write('argv=' + json.dumps(sys.argv) + chr(10))
                fh.write('env=' + os.environ.get('E2E_CASHU_TOKEN', '') + chr(10))
            print(json.dumps({{'run_id': 42, 'db': 'fake'}}))
            """
        )
    )

    class Cfg:
        db_path = tmp_path / "runs.db"
        scenarios_dir = tmp_path / "scenarios"
        logs_dir = tmp_path / "logs"
        compose_file = tmp_path / "compose.yml"
        orchestrate_cmd = [sys.executable, str(fake_orchestrator)]
        cors_origins = ["http://localhost:5173"]

    run_id = spawn_orchestrator(scenario_id="smoke", token=TOKEN, config=Cfg)
    assert run_id == 42

    text = capture.read_text()
    assert "argv=" in text and "env=" in text
    argv_line = [l for l in text.splitlines() if l.startswith("argv=")][0]
    env_line = [l for l in text.splitlines() if l.startswith("env=")][0]
    assert TOKEN not in argv_line, "token was passed on argv — never do this"
    assert TOKEN in env_line, "token must be passed via E2E_CASHU_TOKEN"


def test_redaction_filter_scrubs_cashu_logs(config, caplog):
    """Belt-and-braces: if anyone ever logs a cashu-flavored line, the
    redaction filter installed by create_app must replace it.
    """
    import logging

    create_app(config=config)
    logger = logging.getLogger("server")
    logger.setLevel(logging.INFO)
    with caplog.at_level(logging.INFO, logger="server"):
        logger.info("received cashu token from client: %s", TOKEN)
    for record in caplog.records:
        assert TOKEN not in record.getMessage(), (
            "redaction filter did not scrub a cashu-flavored log line"
        )


def test_grep_repo_for_token_storage():
    """CI-style grep: ensure no source file under server/ tries to write
    a token to a database column or log line. Catches naive future code
    that does `row.cashu_token = body.cashu_token`.
    """
    server_dir = Path(__file__).resolve().parent.parent / "server"
    banned_substrings = (
        ".cashu_token = ",
        '"cashu_token":',
        "log.*cashu",
    )
    for py in server_dir.rglob("*.py"):
        text = py.read_text()
        for bad in banned_substrings:
            assert bad not in text, (
                f"forbidden token-handling pattern {bad!r} in {py}"
            )
