"""Intensive cheapest-provider routing test across many models.

Drives routstrd's price-based provider selection over a spread of models and
both fee regimes, proving that the daemon ranks the cheaper of node-a / node-b
first and that the ranking follows a `routstr-cli providers update --fee` change.

This exercises the discovery + pricing path only (no paid inference) so it runs
without funding and is safe to repeat. Requires the compose stack up
(`make up`) with both nodes' upstream set to a real provider (openrouter) so
each model carries a non-zero `sats_pricing.max_cost`.

Skips (not fails) when the stack is unreachable, matching tests/cli conventions.
"""
from __future__ import annotations

import subprocess
import time

import httpx
import pytest

NODE_A_EXTERNAL = "http://localhost:8001"
NODE_B_EXTERNAL = "http://localhost:8002"
NODE_A_INTERNAL = "http://node-a:8000"
NODE_B_INTERNAL = "http://node-b:8000"
ROUTSTRD = "http://localhost:8091"
ADMIN_PASSWORD = "test-admin-pw"
CLI_CONTAINER = "routstr-testing-cli-runner-1"
CLI_BIN = "/app/dist/index.js"
OPENROUTER_PROVIDER_ID = "1"  # auto-seeded openrouter provider on both nodes

# A diverse spread of models served by the openrouter upstream on both nodes.
MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "o3-mini-high",
    "claude-3.5-haiku",
    "llama-3.3-70b-instruct",
    "aion-rp-llama-3.1-8b",
    "mistral-medium-3-5",
    "deepseek-chat-v3.1",
    "qwen2.5-vl-72b-instruct",
]


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    cmd = ["docker", "exec", CLI_CONTAINER, "bun", CLI_BIN, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _login(node_external: str) -> str:
    with httpx.Client(base_url=node_external, timeout=10) as c:
        r = c.post("/admin/api/login", json={"password": ADMIN_PASSWORD})
        if r.status_code != 200:
            pytest.skip(f"admin login unavailable on {node_external}: {r.status_code}")
        return r.json()["token"]


def _set_fee(node_internal: str, token: str, fee: float) -> None:
    r = _run_cli(
        "--node", node_internal, "-o", "json",
        "providers", "update", OPENROUTER_PROVIDER_ID,
        "-t", token, "--fee", str(fee),
    )
    assert r.returncode == 0, f"cli fee update failed: {r.stderr}\n{r.stdout}"


def _refresh_routstrd() -> None:
    """Force routstrd to re-fetch node catalogs so new fees take effect."""
    try:
        httpx.get(f"{ROUTSTRD}/v1/models", params={"refresh": "true"}, timeout=90)
    except httpx.HTTPError:
        pass


def _ranking(model: str) -> list[tuple[str, float]]:
    """Return [(baseUrl, max_cost), ...] cheapest-first as routstrd ranks them."""
    r = httpx.get(f"{ROUTSTRD}/models/{model}/providers", timeout=30)
    if r.status_code != 200:
        return []
    providers = (r.json().get("output") or {}).get("providers") or []
    return [(p["baseUrl"], float(p["pricing"]["max_cost"])) for p in providers]


@pytest.fixture(scope="module", autouse=True)
def _require_stack():
    try:
        if httpx.get(f"{ROUTSTRD}/health", timeout=5).status_code >= 500:
            pytest.skip("routstrd not reachable; run `make up`")
        if httpx.get(f"{NODE_A_EXTERNAL}/v1/info", timeout=5).status_code >= 500:
            pytest.skip("node-a not reachable; run `make up`")
    except httpx.HTTPError:
        pytest.skip("compose stack not reachable; run `make up`")


def _apply_regime(node_a_fee: float, node_b_fee: float) -> None:
    ta, tb = _login(NODE_A_EXTERNAL), _login(NODE_B_EXTERNAL)
    _set_fee(NODE_A_INTERNAL, ta, node_a_fee)
    _set_fee(NODE_B_INTERNAL, tb, node_b_fee)
    # let nodes re-price + republish, then refresh routstrd's cache
    time.sleep(2)
    _refresh_routstrd()


class TestNodeACheapest:
    """node-a fee < node-b fee  =>  routstrd ranks node-a first for every model."""

    @pytest.fixture(scope="class", autouse=True)
    def regime(self):
        _apply_regime(node_a_fee=0.30, node_b_fee=0.40)

    @pytest.mark.parametrize("model", MODELS)
    def test_node_a_is_cheapest(self, model: str):
        ranking = _ranking(model)
        if len(ranking) < 2:
            pytest.skip(f"model {model} not served by both nodes (got {len(ranking)})")
        cheapest_url, cheapest_cost = ranking[0]
        assert "node-a" in cheapest_url, (
            f"{model}: expected node-a cheapest, got {ranking}"
        )
        # strictly cheaper than node-b (lower fee => lower max_cost)
        assert cheapest_cost < ranking[1][1], f"{model}: not strictly cheaper: {ranking}"


class TestNodeBCheapest:
    """Flip fees: node-b fee < node-a fee  =>  routstrd ranks node-b first."""

    @pytest.fixture(scope="class", autouse=True)
    def regime(self):
        _apply_regime(node_a_fee=0.45, node_b_fee=0.35)

    @pytest.mark.parametrize("model", MODELS)
    def test_node_b_is_cheapest(self, model: str):
        ranking = _ranking(model)
        if len(ranking) < 2:
            pytest.skip(f"model {model} not served by both nodes (got {len(ranking)})")
        cheapest_url, cheapest_cost = ranking[0]
        assert "node-b" in cheapest_url, (
            f"{model}: expected node-b cheapest, got {ranking}"
        )
        assert cheapest_cost < ranking[1][1], f"{model}: not strictly cheaper: {ranking}"
