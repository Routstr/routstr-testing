"""API tests for /api/runs — listing, detail, log retrieval, and create.

The orchestrator subprocess is replaced by an injected runner_fn so the
test doesn't have to spawn pytest. A separate test covers the *real*
subprocess invocation against a stub orchestrate command (see
test_server_token_hygiene).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from runner.models import Run, TestResult, get_engine, get_session
from server.config import ServerConfig
from server.main import create_app


@pytest.fixture()
def setup(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "smoke.yaml").write_text("id: smoke\nname: Smoke\n")
    db_path = tmp_path / "runs.db"
    engine = get_engine(db_path)
    config = ServerConfig(
        scenarios_dir=scenarios_dir,
        db_path=db_path,
        logs_dir=tmp_path / "logs",
        compose_file=tmp_path / "compose.yml",
        orchestrate_cmd=["true"],
        cors_origins=["http://localhost:5173"],
    )
    return config, engine


def _seed_run(engine, **overrides) -> int:
    with get_session(engine) as session:
        row = Run(
            scenario_id=overrides.get("scenario_id", "smoke"),
            started_at=overrides.get("started_at", datetime.utcnow()),
            finished_at=overrides.get("finished_at"),
            status=overrides.get("status", "passed"),
            artifacts_dir=overrides.get("artifacts_dir"),
            vendor_commits_json=overrides.get("vendor_commits_json"),
            token_consumed_sats=overrides.get("token_consumed_sats", 0),
            error_message=overrides.get("error_message"),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def test_list_empty(setup):
    config, _ = setup
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_seeded_runs(setup):
    config, engine = setup
    _seed_run(engine, scenario_id="smoke", status="passed")
    _seed_run(engine, scenario_id="other", status="failed")
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get("/api/runs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    # newest first
    assert data[0]["id"] > data[1]["id"]


def test_list_filters(setup):
    config, engine = setup
    _seed_run(engine, scenario_id="smoke", status="passed")
    _seed_run(engine, scenario_id="other", status="failed")
    _seed_run(engine, scenario_id="smoke", status="failed")
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get("/api/runs?status=failed")
    assert r.status_code == 200
    assert {row["status"] for row in r.json()} == {"failed"}
    r = c.get("/api/runs?scenario_id=smoke")
    assert {row["scenario_id"] for row in r.json()} == {"smoke"}


def test_list_pagination(setup):
    config, engine = setup
    for _ in range(5):
        _seed_run(engine, scenario_id="smoke", status="passed")
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get("/api/runs?limit=2")
    assert len(r.json()) == 2
    r = c.get("/api/runs?limit=2&offset=2")
    assert len(r.json()) == 2


def test_get_detail_with_test_results(setup):
    config, engine = setup
    run_id = _seed_run(
        engine,
        scenario_id="smoke",
        status="failed",
        vendor_commits_json='{"routstr-core": "abc"}',
    )
    with get_session(engine) as session:
        session.add(
            TestResult(
                run_id=run_id,
                test_name="test_x",
                outcome="failed",
                duration_ms=12,
                error_excerpt="boom",
            )
        )
        session.commit()
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["vendor_commits"] == {"routstr-core": "abc"}
    assert len(body["test_results"]) == 1
    assert body["test_results"][0]["test_name"] == "test_x"


def test_get_detail_missing(setup):
    config, _ = setup
    app = create_app(config=config)
    c = TestClient(app)
    assert c.get("/api/runs/9999").status_code == 404


def test_log_listing_and_fetch(setup, tmp_path: Path):
    config, engine = setup
    artifacts = tmp_path / "logs" / "run1"
    artifacts.mkdir(parents=True)
    (artifacts / "pytest.log").write_text("hello world")
    (artifacts / "sync.log").write_text("sync output")
    run_id = _seed_run(engine, artifacts_dir=str(artifacts))
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get(f"/api/runs/{run_id}/logs")
    assert r.status_code == 200
    assert sorted(r.json()["files"]) == ["pytest.log", "sync.log"]
    r = c.get(f"/api/runs/{run_id}/logs/pytest.log")
    assert r.status_code == 200
    assert r.text == "hello world"


def test_log_rejects_path_traversal(setup, tmp_path: Path):
    """If a log name slips past URL normalization with a slash or
    parent-dir prefix in it, the handler must reject it before opening
    the file.
    """
    from server.runs import get_log

    config, engine = setup
    artifacts = tmp_path / "logs" / "run2"
    artifacts.mkdir(parents=True)
    sibling = tmp_path / "logs" / "secret.txt"
    sibling.write_text("should never be served")
    run_id = _seed_run(engine, artifacts_dir=str(artifacts))

    # Call the handler directly with a malicious name (bypasses httpx URL
    # normalization, which would otherwise mangle the test request).
    app = create_app(config=config)
    fake_request = type(
        "R", (), {"app": type("A", (), {"state": app.state})()}
    )()
    from fastapi import HTTPException

    for evil in ("../secret.txt", "..\\secret.txt", "..", "/etc/passwd"):
        with pytest.raises(HTTPException) as exc:
            get_log(run_id, evil, fake_request)
        assert exc.value.status_code in (400, 404)


def test_log_missing_file(setup, tmp_path: Path):
    config, engine = setup
    artifacts = tmp_path / "logs" / "run3"
    artifacts.mkdir(parents=True)
    run_id = _seed_run(engine, artifacts_dir=str(artifacts))
    app = create_app(config=config)
    c = TestClient(app)
    r = c.get(f"/api/runs/{run_id}/logs/missing.log")
    assert r.status_code == 404


def test_create_run_spawns_orchestrator_and_lists(setup):
    config, engine = setup
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
        seen["scenario_id"] = scenario_id
        seen["token"] = token
        seen["target_profile"] = target_profile
        seen["remote_node_urls"] = remote_node_urls
        seen["remote_admin_tokens"] = remote_admin_tokens
        return _seed_run(engine, scenario_id=scenario_id, status="passed")

    app = create_app(config=config, orchestrate_runner=fake_runner)
    c = TestClient(app)
    r = c.post(
        "/api/runs",
        json={"scenario_id": "smoke", "cashu_token": "cashuABCD"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scenario_id"] == "smoke"
    assert seen["scenario_id"] == "smoke"
    assert seen["token"] == "cashuABCD"
    assert seen["target_profile"] is None
    assert seen["remote_node_urls"] is None
    assert seen["remote_admin_tokens"] is None

    # Confirm acceptance criterion: the new run appears in GET /api/runs
    listing = c.get("/api/runs").json()
    assert listing[0]["id"] == body["run_id"]
    assert listing[0]["scenario_id"] == "smoke"


def test_create_run_unknown_scenario_returns_404(setup):
    config, _ = setup
    app = create_app(config=config)
    c = TestClient(app)
    r = c.post(
        "/api/runs",
        json={"scenario_id": "does-not-exist", "cashu_token": "cashuX"},
    )
    assert r.status_code == 404


def test_health(setup):
    config, _ = setup
    app = create_app(config=config)
    c = TestClient(app)
    assert c.get("/api/health").json() == {"status": "ok"}


def test_create_run_remote_profile_forwards_urls_and_tokens(setup):
    """POST /api/runs with target_profile=remote forwards URLs + tokens
    (admin tokens via the runner's kwargs, never persisted to runs.db).
    """
    config, engine = setup
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
        seen.update(
            scenario_id=scenario_id,
            target_profile=target_profile,
            remote_node_urls=remote_node_urls,
            remote_admin_tokens=remote_admin_tokens,
        )
        # Mirror what the orchestrator does: persist target_profile + URLs.
        import json

        with get_session(engine) as session:
            row = Run(
                scenario_id=scenario_id,
                status="passed",
                target_profile=target_profile or "local",
                remote_node_urls_json=(
                    json.dumps(remote_node_urls) if remote_node_urls else None
                ),
            )
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
            "cashu_token": "cashuREMOTE",
            "target_profile": "remote",
            "remote_node_urls": ["https://node1.example", "https://node2.example"],
            "remote_admin_tokens": ["admin-1", "admin-2"],
        },
    )
    assert r.status_code == 201, r.text
    assert seen["target_profile"] == "remote"
    assert seen["remote_node_urls"] == ["https://node1.example", "https://node2.example"]
    assert seen["remote_admin_tokens"] == ["admin-1", "admin-2"]

    # The Run summary surfaces target_profile + URLs.
    runs = c.get("/api/runs").json()
    me = next(r for r in runs if r["scenario_id"] == "smoke")
    assert me["target_profile"] == "remote"
    assert me["remote_node_urls"] == [
        "https://node1.example",
        "https://node2.example",
    ]

    # Listing filter by target_profile narrows correctly.
    local_only = c.get("/api/runs?target_profile=local").json()
    assert all(r["target_profile"] == "local" for r in local_only)
    remote_only = c.get("/api/runs?target_profile=remote").json()
    assert remote_only and all(r["target_profile"] == "remote" for r in remote_only)


def test_create_run_remote_profile_requires_urls(setup):
    """target_profile=remote without any URLs is a 400."""
    config, _ = setup

    def fake_runner(**_kw):  # would be a bug if this is reached
        raise AssertionError("orchestrator should not be invoked on validation error")

    app = create_app(config=config, orchestrate_runner=fake_runner)
    c = TestClient(app)
    r = c.post(
        "/api/runs",
        json={
            "scenario_id": "smoke",
            "cashu_token": "cashuABCD",
            "target_profile": "remote",
        },
    )
    assert r.status_code == 400, r.text
    assert "remote" in r.json()["detail"].lower()


def test_create_run_unknown_target_profile_rejected(setup):
    config, _ = setup
    app = create_app(config=config, orchestrate_runner=lambda **_kw: 0)
    c = TestClient(app)
    r = c.post(
        "/api/runs",
        json={
            "scenario_id": "smoke",
            "cashu_token": "cashuABCD",
            "target_profile": "cloud",
        },
    )
    assert r.status_code == 400, r.text
    assert "target_profile" in r.json()["detail"]
