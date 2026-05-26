"""API tests for /api/scenarios CRUD against on-disk YAML."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.config import ServerConfig
from server.main import create_app


@pytest.fixture()
def app_factory(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    config = ServerConfig(
        scenarios_dir=scenarios_dir,
        db_path=tmp_path / "runs.db",
        logs_dir=tmp_path / "logs",
        compose_file=tmp_path / "compose.yml",
        orchestrate_cmd=["true"],
        cors_origins=["http://localhost:5173"],
    )

    def _make(runner=None):
        app = create_app(config=config, orchestrate_runner=runner)
        return app, config

    return _make


@pytest.fixture()
def client(app_factory):
    app, config = app_factory()
    return TestClient(app), config


def _seed(scenarios_dir: Path, scenario_id: str, body: str) -> Path:
    path = scenarios_dir / f"{scenario_id}.yaml"
    path.write_text(body)
    return path


def test_list_empty(client):
    c, _ = client
    r = c.get("/api/scenarios")
    assert r.status_code == 200
    assert r.json() == []


def test_list_after_seed(client):
    c, cfg = client
    _seed(
        cfg.scenarios_dir,
        "smoke",
        "id: smoke\nname: Smoke\ndescription: a test\n",
    )
    r = c.get("/api/scenarios")
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == "smoke"
    assert item["name"] == "Smoke"
    assert item["description"] == "a test"
    # ROU-138: telemetry fields default to zeros for a never-run scenario.
    assert item["expected_cost_sats"] == 0
    assert item["stats"]["runs_count"] == 0
    assert item["stats"]["avg_consumed_sats"] == 0
    assert item["stats"]["last_consumed_sats"] is None


def test_get_detail(client):
    c, cfg = client
    body = "id: smoke\nname: Smoke\n"
    _seed(cfg.scenarios_dir, "smoke", body)
    r = c.get("/api/scenarios/smoke")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "smoke"
    assert data["yaml"] == body


def test_get_missing_returns_404(client):
    c, _ = client
    assert c.get("/api/scenarios/nope").status_code == 404


def test_create_then_get(client):
    c, cfg = client
    payload = {"id": "new", "yaml": "id: new\nname: New\n"}
    r = c.post("/api/scenarios", json=payload)
    assert r.status_code == 201, r.text
    assert (cfg.scenarios_dir / "new.yaml").read_text() == payload["yaml"]
    g = c.get("/api/scenarios/new")
    assert g.status_code == 200
    assert g.json()["name"] == "New"


def test_create_conflict(client):
    c, cfg = client
    _seed(cfg.scenarios_dir, "dup", "id: dup\nname: Dup\n")
    r = c.post("/api/scenarios", json={"id": "dup", "yaml": "id: dup\nname: x\n"})
    assert r.status_code == 409


def test_create_rejects_bad_id(client):
    c, _ = client
    r = c.post(
        "/api/scenarios",
        json={"id": "../escape", "yaml": "id: x\nname: y\n"},
    )
    assert r.status_code == 400


def test_create_rejects_invalid_yaml(client):
    c, _ = client
    r = c.post("/api/scenarios", json={"id": "broken", "yaml": "key: : :"})
    assert r.status_code == 422


def test_update_writes_back_to_disk(client):
    c, cfg = client
    _seed(cfg.scenarios_dir, "smoke", "id: smoke\nname: Old\n")
    new_body = "id: smoke\nname: Updated\ndescription: changed\n"
    r = c.put("/api/scenarios/smoke", json={"yaml": new_body})
    assert r.status_code == 200
    assert (cfg.scenarios_dir / "smoke.yaml").read_text() == new_body
    assert r.json()["name"] == "Updated"


def test_update_missing_returns_404(client):
    c, _ = client
    r = c.put("/api/scenarios/nope", json={"yaml": "id: nope\nname: n\n"})
    assert r.status_code == 404


def test_delete(client):
    c, cfg = client
    _seed(cfg.scenarios_dir, "smoke", "id: smoke\nname: Smoke\n")
    r = c.delete("/api/scenarios/smoke")
    assert r.status_code == 204
    assert not (cfg.scenarios_dir / "smoke.yaml").exists()
    assert c.get("/api/scenarios/smoke").status_code == 404


def test_delete_missing_returns_404(client):
    c, _ = client
    assert c.delete("/api/scenarios/nope").status_code == 404


# ─── ROU-138: token-budget telemetry on scenarios ──────────────────────────


def _seed_run(engine, *, scenario_id: str, consumed: int, status_: str = "passed"):
    from datetime import datetime

    from runner.models import Run, get_session

    with get_session(engine) as session:
        row = Run(
            scenario_id=scenario_id,
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
            status=status_,
            token_consumed_sats=consumed,
        )
        session.add(row)
        session.commit()


def test_list_includes_expected_cost_sats_from_yaml(client):
    c, cfg = client
    _seed(
        cfg.scenarios_dir,
        "paid",
        "id: paid\nname: Paid\nexpected_cost_sats: 1500\n",
    )
    payload = c.get("/api/scenarios").json()
    assert payload[0]["expected_cost_sats"] == 1500


def test_scenario_stats_reflect_three_runs_average(app_factory):
    """ROU-138 acceptance: after three runs of the same scenario, the
    Scenarios listing shows the historical average consumed sats."""
    app, cfg = app_factory()
    _seed(
        cfg.scenarios_dir,
        "golden",
        "id: golden\nname: Golden\nexpected_cost_sats: 500\n",
    )
    engine = app.state.engine
    _seed_run(engine, scenario_id="golden", consumed=300)
    _seed_run(engine, scenario_id="golden", consumed=600)
    _seed_run(engine, scenario_id="golden", consumed=900)

    c = TestClient(app)
    payload = c.get("/api/scenarios").json()
    item = next(x for x in payload if x["id"] == "golden")
    assert item["stats"]["runs_count"] == 3
    assert item["stats"]["avg_consumed_sats"] == 600  # (300+600+900) / 3
    assert item["stats"]["last_consumed_sats"] == 900  # most-recent finished_at
    assert item["expected_cost_sats"] == 500


def test_scenario_stats_segregate_by_scenario_id(app_factory):
    app, cfg = app_factory()
    _seed(cfg.scenarios_dir, "alpha", "id: alpha\nname: Alpha\n")
    _seed(cfg.scenarios_dir, "beta", "id: beta\nname: Beta\n")
    _seed_run(app.state.engine, scenario_id="alpha", consumed=100)
    _seed_run(app.state.engine, scenario_id="alpha", consumed=200)
    _seed_run(app.state.engine, scenario_id="beta", consumed=999)

    c = TestClient(app)
    payload = {x["id"]: x for x in c.get("/api/scenarios").json()}
    assert payload["alpha"]["stats"]["runs_count"] == 2
    assert payload["alpha"]["stats"]["avg_consumed_sats"] == 150
    assert payload["beta"]["stats"]["runs_count"] == 1
    assert payload["beta"]["stats"]["avg_consumed_sats"] == 999


def test_scenario_detail_includes_stats(app_factory):
    app, cfg = app_factory()
    _seed(cfg.scenarios_dir, "solo", "id: solo\nname: Solo\nexpected_cost_sats: 42\n")
    _seed_run(app.state.engine, scenario_id="solo", consumed=10)
    _seed_run(app.state.engine, scenario_id="solo", consumed=30)

    c = TestClient(app)
    payload = c.get("/api/scenarios/solo").json()
    assert payload["expected_cost_sats"] == 42
    assert payload["stats"]["runs_count"] == 2
    assert payload["stats"]["avg_consumed_sats"] == 20
    assert payload["stats"]["last_consumed_sats"] == 30
