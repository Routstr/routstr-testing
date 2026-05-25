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
    assert payload == [{"id": "smoke", "name": "Smoke", "description": "a test"}]


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
