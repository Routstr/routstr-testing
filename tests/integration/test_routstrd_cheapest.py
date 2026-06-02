"""Intensive cheapest-provider routing test across many models.

Drives routstrd's price-based provider selection over a spread of models and
both fee regimes, proving that the daemon ranks the cheaper of node-a / node-b
first and that the ranking follows a `routstr-cli providers update --fee` change.

This exercises the discovery + pricing path only (no paid inference) so it runs
without funding and is safe to repeat.

Targets resolve via `tests.integration.targets`: the local compose stack by
default, or remote nodes under `TARGET_PROFILE=remote` (`REMOTE_NODE_URLS`,
`REMOTE_NODE_ADMIN_TOKEN_<i>`, `ROUTSTRD_URL`). It mutates each node's fee with
`routstr-cli`, so it needs an admin token per node and a reachable cli-runner;
it SKIPS (not fails) when those, the nodes, or routstrd are unavailable —
e.g. a remote node without an admin token.
"""
from __future__ import annotations

import subprocess
import time

import httpx
import pytest

from tests.integration import targets

NODE_A_EXTERNAL = targets.node_api_url(0)
NODE_B_EXTERNAL = targets.node_api_url(1)
NODE_A_INTERNAL = targets.node_cli_url(0)
NODE_B_INTERNAL = targets.node_cli_url(1)
ROUTSTRD = targets.routstrd_url()
CLI_CONTAINER = targets.cli_runner_container()
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
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        pytest.skip(f"routstr-cli runner unavailable ({CLI_CONTAINER}): {exc}")


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
    if targets.node_count() < 2:
        pytest.skip("need >= 2 nodes (set REMOTE_NODE_URLS or run the local stack)")
    try:
        if httpx.get(f"{ROUTSTRD}/health", timeout=5).status_code >= 500:
            pytest.skip(f"routstrd not reachable at {ROUTSTRD}")
        if not targets.node_reachable(0) or not targets.node_reachable(1):
            pytest.skip("both nodes must be reachable")
    except httpx.HTTPError:
        pytest.skip("target stack not reachable")


def _apply_regime(node_a_fee: float, node_b_fee: float) -> None:
    ta, tb = targets.admin_token(0), targets.admin_token(1)
    if not ta or not tb:
        pytest.skip("admin token unavailable for both nodes (set REMOTE_NODE_ADMIN_TOKEN_<i>)")
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
        assert targets.node_marker(0) in cheapest_url, (
            f"{model}: expected node 0 ({targets.node_marker(0)}) cheapest, got {ranking}"
        )
        # strictly cheaper than node 1 (lower fee => lower max_cost)
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
        assert targets.node_marker(1) in cheapest_url, (
            f"{model}: expected node 1 ({targets.node_marker(1)}) cheapest, got {ranking}"
        )
        assert cheapest_cost < ranking[1][1], f"{model}: not strictly cheaper: {ranking}"
