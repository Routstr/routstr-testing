"""GET /api/balance — best-effort routstrd balance estimate.

The React UI uses this to warn when a scenario's `expected_cost_sats`
exceeds the daemon's available funds. We never persist the cashu token,
so "balance" here is *current routstrd state* (wallet + cached tokens
+ api keys), not "balance the user originally provided".

When routstrd is unreachable (e.g., compose isn't up yet, or the UI is
queried before a topup), we return 200 with `total_sats: null` and
`source: "unavailable"` so the UI can show "balance unknown" instead of
blocking the run.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from runner.balance import fetch_total_sats

from .schemas import BalanceEstimate

router = APIRouter(prefix="/api/balance", tags=["balance"])


@router.get("", response_model=BalanceEstimate)
def get_balance(request: Request) -> BalanceEstimate:
    fetcher = getattr(request.app.state, "balance_fetcher", None) or fetch_total_sats
    routstrd_url = getattr(request.app.state.config, "routstrd_url", None)
    try:
        total = fetcher(routstrd_url) if routstrd_url else fetcher()
    except Exception as exc:  # don't surface fetcher internals as 500s
        return BalanceEstimate(
            total_sats=None,
            source="unavailable",
            fetched_at=datetime.utcnow(),
            detail=f"fetcher raised: {exc!r}",
        )
    if total is None:
        return BalanceEstimate(
            total_sats=None,
            source="unavailable",
            fetched_at=datetime.utcnow(),
            detail="routstrd /keys/balance unreachable",
        )
    return BalanceEstimate(
        total_sats=int(total),
        source="routstrd",
        fetched_at=datetime.utcnow(),
    )
