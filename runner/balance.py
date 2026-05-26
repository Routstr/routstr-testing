"""Best-effort routstrd balance fetcher used for token-budget telemetry.

Returns the total sats currently held by routstrd (wallet + cached tokens
+ API key balances) by polling `GET <ROUTSTRD_URL>/keys/balance`. We use
the aggregate total — not just wallet — because sats flow through
wallet → cached tokens → API keys as the daemon serves inference, and
"how much has the user spent" is conserved across those bins.

All calls are best-effort: if routstrd isn't reachable, returns None and
the caller should treat the run's `token_consumed_sats` as 0 rather than
crash. The orchestrator runs in environments (`smoke`, unit tests) where
routstrd isn't up at all.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_ROUTSTRD_URL = "http://localhost:8091"
DEFAULT_TIMEOUT_SECONDS = 5.0


def routstrd_url() -> str:
    return os.environ.get("ROUTSTRD_URL", DEFAULT_ROUTSTRD_URL).rstrip("/")


def fetch_total_sats(
    base_url: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> int | None:
    """Return routstrd's aggregate balance in sats, or None if unavailable.

    Shape of `/keys/balance` (from routstrd daemon http/index.ts):

        { "output": { "keys": [...], "total": <int>, "unit": "sat",
                       "apikeysCalled": <int> } }
    """
    url = (base_url or routstrd_url()) + "/keys/balance"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None

    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, dict):
        return None
    total = output.get("total")
    if not isinstance(total, (int, float)):
        return None
    return int(total)
