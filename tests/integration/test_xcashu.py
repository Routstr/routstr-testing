"""X-Cashu (pay-per-request) payment mode against a routstr node.

Unlike the Bearer api-key / balance path, X-Cashu is single-use ecash: the
client sends a cashu token in the `X-Cashu` request header, the node redeems it,
charges the exact request cost, and returns the **change** as a fresh cashu
token in the `X-Cashu` response header. No balance is kept on the node.

What this verifies:
  * a single X-Cashu payment produces a real completion for several models,
    each paid with its own funded token;
  * the node returns a change token in the `X-Cashu` response header;
  * that change token is itself fresh, spendable ecash (pays a second request).

Needs X_CASHU_TOKENS — a comma-separated list of funded cashu tokens, one per
model plus one for the change test — and the openrouter-backed stack up. Skips
otherwise. Marked `requires_funded_daemon`; spends real (sub-)sats.
"""
from __future__ import annotations

import os

import httpx
import pytest

from tests.integration import spend, targets

NODE_A_EXTERNAL = targets.node_api_url(0)  # local :8001 or first remote node
TOKENS = [t.strip() for t in os.environ.get("X_CASHU_TOKENS", "").split(",") if t.strip()]

# One funded token per model (cheap models so a small token covers max_cost).
MODELS = [
    "gpt-4o-mini",
    "llama-3.3-70b-instruct",
    "deepseek-chat-v3.1",
    "aion-rp-llama-3.1-8b",
]

pytestmark = pytest.mark.requires_funded_daemon


@pytest.fixture(scope="module", autouse=True)
def _require_funded():
    if len(TOKENS) < len(MODELS) + 1:
        pytest.skip(
            f"need >= {len(MODELS) + 1} tokens in X_CASHU_TOKENS, got {len(TOKENS)}"
        )
    try:
        if httpx.get(f"{NODE_A_EXTERNAL}/v1/info", timeout=5).status_code >= 500:
            pytest.skip("node-a not reachable; run `make up`")
    except httpx.HTTPError:
        pytest.skip("node-a not reachable; run `make up`")


def _xcashu_chat(token: str, model: str, prompt: str = "Reply with one short sentence.") -> httpx.Response:
    return httpx.post(
        f"{NODE_A_EXTERNAL}/v1/chat/completions",
        headers={"X-Cashu": token},  # no Authorization — pay-per-request
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 40},
        timeout=90,
    )


@pytest.mark.parametrize("idx,model", list(enumerate(MODELS)))
def test_xcashu_pay_per_request(idx: int, model: str):
    """A single X-Cashu payment yields a real completion + a change token."""
    r = _xcashu_chat(TOKENS[idx], model)
    assert r.status_code == 200, f"{model}: HTTP {r.status_code}: {r.text[:300]}"

    body = r.json()
    assert body.get("usage", {}).get("total_tokens", 0) > 0, f"{model}: not billed"
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message", {}).get("content") or "").strip()
    assert content or choice.get("finish_reason") == "length", (
        f"{model}: empty completion: {str(body)[:200]}"
    )

    change = r.headers.get("x-cashu")
    assert change and change.startswith("cashu"), f"{model}: no X-Cashu change token returned"
    assert change != TOKENS[idx], f"{model}: change identical to input token"

    # Report the sats actually spent: input token amount minus the change.
    try:
        spent = spend.token_amount_sats(TOKENS[idx]) - spend.token_amount_sats(change)
        spend.record_sats(max(0, spent))
    except ValueError:
        pass


def test_xcashu_change_is_spendable():
    """The change token returned by X-Cashu is itself fresh, spendable ecash."""
    first = _xcashu_chat(TOKENS[len(MODELS)], "gpt-4o-mini")
    assert first.status_code == 200, f"setup request failed: {first.text[:200]}"
    change = first.headers.get("x-cashu")
    assert change and change.startswith("cashu"), "no change token to re-spend"

    # The change pays a second, independent request.
    second = _xcashu_chat(change, "gpt-4o-mini")
    assert second.status_code == 200, f"change not spendable: {second.text[:200]}"
