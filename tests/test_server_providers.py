"""API tests for GET /api/providers and POST /api/runs upstream forwarding."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runner.models import Run, get_engine, get_session
from server.config import ServerConfig
from server.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent


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
        providers_dir=REPO_ROOT / "providers",
    )


def test_list_providers_returns_shipped_set(config):
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get("/api/providers")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert {"openai", "anthropic", "openrouter"} <= ids


def test_provider_exposes_required_env_keys_not_values(config):
    app = create_app(config=config)
    c = TestClient(app)
    body = next(p for p in c.get("/api/providers").json() if p["id"] == "openrouter")
    names = {e["name"] for e in body["required_env"]}
    assert names == {"OPENROUTER_API_KEY", "OPENROUTER_REFERER"}
    key = next(e for e in body["required_env"] if e["name"] == "OPENROUTER_API_KEY")
    assert key["secret"] is True
    assert body["models"], "models catalog should be surfaced for the UI"


def test_create_run_forwards_upstream_profile_and_keeps_key_secret(config):
    seen: dict = {}

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
        seen.update(upstream_profile=upstream_profile, upstream_env=upstream_env)
        from datetime import datetime

        engine = get_engine(config.db_path)
        with get_session(engine) as session:
            row = Run(
                scenario_id=scenario_id,
                started_at=datetime.utcnow(),
                status="passed",
                upstream_profile=upstream_profile or "mock",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    app = create_app(config=config, orchestrate_runner=fake_runner)
    c = TestClient(app)
    secret = "sk-super-secret-key-xyz"
    r = c.post(
        "/api/runs",
        json={
            "scenario_id": "smoke",
            "cashu_token": "cashuABCD",
            "upstream_profile": "openai",
            "upstream_env": {"OPENAI_API_KEY": secret},
        },
    )
    assert r.status_code == 201, r.text
    assert seen["upstream_profile"] == "openai"
    assert seen["upstream_env"] == {"OPENAI_API_KEY": secret}

    # The key must never be echoed back or persisted.
    assert secret not in r.text
    with sqlite3.connect(config.db_path) as conn:
        rows = conn.execute("SELECT * FROM runs").fetchall()
    assert secret not in repr(rows)

    # The run summary surfaces the upstream profile label.
    summary = c.get("/api/runs").json()[0]
    assert summary["upstream_profile"] == "openai"


def test_create_run_drops_blank_upstream_env_values(config):
    seen: dict = {}

    def fake_runner(*, scenario_id, token, config, upstream_env=None, **_kw):
        seen["upstream_env"] = upstream_env
        from datetime import datetime

        engine = get_engine(config.db_path)
        with get_session(engine) as session:
            row = Run(scenario_id=scenario_id, started_at=datetime.utcnow(), status="passed")
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    app = create_app(config=config, orchestrate_runner=fake_runner)
    c = TestClient(app)
    r = c.post(
        "/api/runs",
        json={
            "scenario_id": "smoke",
            "cashu_token": "cashuABCD",
            "upstream_env": {"OPENAI_API_KEY": "", "EMPTY": ""},
        },
    )
    assert r.status_code == 201, r.text
    # All-blank env collapses to None so it can't shadow a real server-side key.
    assert seen["upstream_env"] is None
