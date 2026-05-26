"""API tests for GET /api/balance — UI run-modal warning telemetry (ROU-138)."""

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

    def _make(fetcher=None):
        return create_app(config=config, balance_fetcher=fetcher), config

    return _make


def test_balance_returns_total_sats_from_fetcher(app_factory):
    app, _ = app_factory(fetcher=lambda *a, **kw: 12345)
    c = TestClient(app)
    r = c.get("/api/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sats"] == 12345
    assert body["source"] == "routstrd"
    assert body["fetched_at"]


def test_balance_returns_unavailable_when_fetcher_returns_none(app_factory):
    app, _ = app_factory(fetcher=lambda *a, **kw: None)
    c = TestClient(app)
    r = c.get("/api/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sats"] is None
    assert body["source"] == "unavailable"
    assert "unreachable" in (body.get("detail") or "")


def test_balance_returns_unavailable_when_fetcher_raises(app_factory):
    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    app, _ = app_factory(fetcher=boom)
    c = TestClient(app)
    r = c.get("/api/balance")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sats"] is None
    assert body["source"] == "unavailable"
    assert "connection refused" in (body.get("detail") or "")


def test_balance_uses_configured_routstrd_url(tmp_path: Path):
    """The configured routstrd_url is passed to the fetcher so deployments
    pointing at a non-default host work without monkeypatching env."""
    captured = {}

    def fetcher(url=None, *a, **kw):
        captured["url"] = url
        return 7

    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    config = ServerConfig(
        scenarios_dir=scenarios_dir,
        db_path=tmp_path / "runs.db",
        logs_dir=tmp_path / "logs",
        compose_file=tmp_path / "compose.yml",
        orchestrate_cmd=["true"],
        cors_origins=["http://localhost:5173"],
        routstrd_url="http://routstrd.internal:9000",
    )
    app = create_app(config=config, balance_fetcher=fetcher)
    c = TestClient(app)
    r = c.get("/api/balance")
    assert r.status_code == 200
    assert captured["url"] == "http://routstrd.internal:9000"
    assert r.json()["total_sats"] == 7
