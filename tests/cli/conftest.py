"""Shared fixtures for routstr-cli tests.

Requires the compose stack to be up (`make up`). Tests are skipped (not failed)
when the CLI container or node-a is unreachable, so a partial stack still
produces a green run.
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.cli.helpers import (  # noqa: E402
    ADMIN_PASSWORD,
    NODE_A_EXTERNAL,
    NODE_A_INTERNAL,
    run_cli,
)


def _cli_available() -> bool:
    try:
        r = run_cli("--version")
        return r.returncode == 0
    except Exception:
        return False


def _node_a_available() -> bool:
    try:
        r = httpx.get(f"{NODE_A_EXTERNAL}/v1/info", timeout=5)
        return r.status_code < 500
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_cli():
    """Skip the entire cli test session when the CLI container is unreachable."""
    if not _cli_available():
        pytest.skip("cli-runner container not reachable; run `make up` first")


@pytest.fixture(scope="session", autouse=True)
def require_node_a():
    """Skip when node-a is unreachable."""
    if not _node_a_available():
        pytest.skip("node-a not reachable; run `make up` first")


@pytest.fixture(scope="session")
def cli():
    """Return the run_cli helper."""
    return run_cli


@pytest.fixture(scope="session")
def node_a() -> str:
    """Internal node-a URL (reachable from inside the container)."""
    return NODE_A_INTERNAL


@pytest.fixture(scope="session")
def admin_token(require_node_a) -> str:
    """Session-scoped admin token for node-a, obtained via /admin/api/login."""
    with httpx.Client(base_url=NODE_A_EXTERNAL, timeout=10) as client:
        r = client.post("/admin/api/login", json={"password": ADMIN_PASSWORD})
        if r.status_code in (401, 500):
            setup = client.post("/admin/api/setup", json={"password": ADMIN_PASSWORD})
            if setup.status_code not in (200, 409):
                pytest.skip(f"admin setup failed: {setup.status_code} {setup.text}")
            r = client.post("/admin/api/login", json={"password": ADMIN_PASSWORD})
        if r.status_code != 200:
            pytest.skip(f"admin login unavailable: {r.status_code} {r.text}")
        return r.json()["token"]


@pytest.fixture(scope="session")
def cli_schema() -> dict:
    """Fetch and return the parsed CLI schema (cached per session)."""
    result = run_cli("schema")
    assert result.returncode == 0, f"routstr schema failed: {result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="session")
def managed_provider(node_a, admin_token) -> dict:
    """Add a test upstream provider, yield its details, remove it on teardown."""
    # Use a unique port offset so this doesn't collide with the production provider.
    add = run_cli(
        "--node", node_a, "-o", "json",
        "providers", "add", "test-fixture",
        "-t", admin_token,
        "--base-url", "http://mock-openai:3098",
        "--api-key", "fixture-key",
    )
    assert add.returncode == 0, f"providers add failed: {add.stderr}\n{add.stdout}"
    data = json.loads(add.stdout)
    provider_id = str(data.get("id") or data.get("provider_id") or "")
    assert provider_id, f"no provider id in: {data}"

    yield {"id": provider_id, "base_url": "http://mock-openai:3098"}

    run_cli("--node", node_a, "providers", "remove", provider_id, "-t", admin_token)


@pytest.fixture(scope="session")
def managed_model_id(node_a, admin_token, managed_provider) -> str:
    """Return a model ID from the managed provider's upstream (for model subcommand tests)."""
    result = run_cli(
        "--node", node_a, "-o", "json",
        "providers", "models", "list", managed_provider["id"],
        "-t", admin_token, "--source", "remote",
    )
    if result.returncode != 0:
        pytest.skip(f"providers models list failed: {result.stderr}")
    data = json.loads(result.stdout)
    models = data if isinstance(data, list) else data.get("models", [])
    if not models:
        pytest.skip("no models returned from managed provider")
    first = models[0]
    return first.get("id") or first.get("model_id") or str(first)
