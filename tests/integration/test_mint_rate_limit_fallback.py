"""End-to-end mint 429 fallback and invoice provenance coverage for PR #597.

The primary mint is the fault proxy. It returns HTTP 429 for mint-quote requests,
forcing routstr-core to create the invoice on the secondary trusted mint. The
scenario then follows the invoice through settlement and verifies that a top-up
for the resulting key skips the primary while the create's 429 rate-limit
cooldown is still active (top-ups are not pinned to a backing mint; they walk
the same fallback candidates, and the cooldown persists on the per-mint guard).
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx
import pytest

from tests.integration.targets import require_node, unavailable

pytestmark = pytest.mark.destructive

NODE = os.environ.get("NODE_A_URL", "http://localhost:8001").rstrip("/")
PROXY_CTL = os.environ.get("FAULT_PROXY_URL", "http://localhost:3340").rstrip("/")
NODE_CONTAINER = os.environ.get("NODE_CONTAINER", "routstr-testing-node-a-1")
PRIMARY_FAULT_MINT = "http://fault-proxy:3340"
SECONDARY_MINT = "http://primary-mint:3338"


@pytest.fixture(scope="module", autouse=True)
def _require_stack() -> None:
    require_node()
    try:
        response = httpx.get(f"{PROXY_CTL}/__proxy__/stats", timeout=5)
        if response.status_code != 200:
            unavailable(f"fault-proxy not reachable at {PROXY_CTL}; run `make up`")
    except httpx.HTTPError:
        unavailable(f"fault-proxy not reachable at {PROXY_CTL}; run `make up`")

    try:
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                NODE_CONTAINER,
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        unavailable(f"cannot verify node mint topology: {exc}")
    expected = f"CASHU_MINTS={PRIMARY_FAULT_MINT},{SECONDARY_MINT}"
    if expected not in inspected.stdout:
        unavailable(
            "rate-limit topology is not active; run "
            "`make mint-rate-limit-fallback-test`"
        )


def _invoice_mint_url(invoice_id: str) -> str:
    script = (
        "import sqlite3; "
        "db=sqlite3.connect('/data/node-a.db'); "
        f"row=db.execute(\"select mint_url from lightning_invoices where id='{invoice_id}'\").fetchone(); "
        "print(row[0] if row else '')"
    )
    try:
        result = subprocess.run(
            ["docker", "exec", NODE_CONTAINER, "python", "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        unavailable(f"cannot inspect node invoice provenance: {exc}")
    return result.stdout.strip()


def _wait_paid(invoice_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{NODE}/lightning/invoice/{invoice_id}/status", timeout=15
        )
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == "paid":
            return last
        time.sleep(1)
    pytest.fail(f"invoice {invoice_id} did not settle: {last}")


def _create_invoice(amount: int, *, purpose: str, api_key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    response = httpx.post(
        f"{NODE}/lightning/invoice",
        json={"amount_sats": amount, "purpose": purpose},
        headers=headers,
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_429_fallback_persists_mint_and_topup_respects_cooldown() -> None:
    reset = httpx.post(
        f"{PROXY_CTL}/__proxy__/reset",
        params={"faults": 10, "kind": "mint_quote_429", "retry_after": "0"},
        timeout=10,
    )
    assert reset.status_code == 200, reset.text

    created = _create_invoice(32, purpose="create")
    create_stats = httpx.get(f"{PROXY_CTL}/__proxy__/stats", timeout=10).json()
    assert create_stats["faulted"] >= 1, create_stats
    assert create_stats["mint_quote_attempts"] >= 1, create_stats
    assert _invoice_mint_url(created["invoice_id"]) == SECONDARY_MINT

    paid = _wait_paid(created["invoice_id"])
    api_key = paid.get("api_key")
    assert api_key and api_key.startswith("sk-")

    attempts_before_topup = httpx.get(
        f"{PROXY_CTL}/__proxy__/stats", timeout=10
    ).json()["mint_quote_attempts"]
    topup = _create_invoice(16, purpose="topup", api_key=api_key)
    attempts_after_topup = httpx.get(
        f"{PROXY_CTL}/__proxy__/stats", timeout=10
    ).json()["mint_quote_attempts"]

    assert attempts_after_topup == attempts_before_topup, (
        "top-up retried the primary during its 429 rate-limit cooldown"
    )
    assert _invoice_mint_url(topup["invoice_id"]) == SECONDARY_MINT
    topup_paid = _wait_paid(topup["invoice_id"])
    assert topup_paid["api_key"] == api_key
