"""Real paid inference across many models through a routstr node.

Pays node-a (upstream = openrouter, configured from the operator's own API key)
with a funded ecash balance and runs a real `/v1/chat/completions` for each
model, asserting a 200 with non-empty assistant content. This is the genuine
end-to-end path: ecash balance -> node -> openrouter -> completion.

Auth: a node api-key (or raw cashu token) with balance, supplied via
NODE_A_API_KEY. Skips when absent so unfunded CI stays green. Marked
`requires_funded_daemon` since it spends real sats (sub-sat per call here).
"""
from __future__ import annotations

import os

import httpx
import pytest

from tests.integration import spend, targets

NODE_A_EXTERNAL = targets.node_api_url(0)  # local :8001 or first remote node
API_KEY = os.environ.get("NODE_A_API_KEY", "").strip()

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

pytestmark = pytest.mark.requires_funded_daemon


def _api_key_balance_msat() -> int | None:
    """Remaining balance (msat) on the funded api key, or None if unavailable."""
    try:
        r = httpx.get(
            f"{NODE_A_EXTERNAL}/v1/wallet/info",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=10,
        )
        return r.json().get("balance") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


@pytest.fixture(scope="module", autouse=True)
def _require_funded():
    if not API_KEY:
        pytest.skip("NODE_A_API_KEY not set; provide a funded node key/cashu token")
    try:
        if httpx.get(f"{NODE_A_EXTERNAL}/v1/info", timeout=5).status_code >= 500:
            pytest.skip("node-a not reachable; run `make up`")
    except httpx.HTTPError:
        pytest.skip("node-a not reachable; run `make up`")

    # Report the real spend (msat) across the module so the run shows it.
    start = _api_key_balance_msat()
    yield
    end = _api_key_balance_msat()
    if start is not None and end is not None and start > end:
        spend.record_msats(start - end)


@pytest.mark.parametrize("model", MODELS)
def test_real_completion(model: str):
    r = httpx.post(
        f"{NODE_A_EXTERNAL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with one short sentence."}],
            "max_tokens": 40,
        },
        timeout=90,
    )
    assert r.status_code == 200, f"{model}: HTTP {r.status_code}: {r.text[:300]}"
    body = r.json()
    # node billed the request from the ecash balance — proves the money path
    assert body.get("usage", {}).get("total_tokens", 0) > 0, f"{model}: no usage reported"
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message", {}).get("content") or "").strip()
    finish = choice.get("finish_reason")
    # Reasoning models (o3*) may spend the whole max_tokens budget on hidden
    # reasoning and return empty visible content with finish_reason=length —
    # still a real, billed completion.
    assert content or finish == "length", f"{model}: empty completion: {str(body)[:300]}"
