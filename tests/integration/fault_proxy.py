"""Fault-injecting reverse proxy in front of a Cashu mint (Phase 2 swap retry).

Transparently forwards every request to PROXY_TARGET (the real foreign mint) so
all crypto/protocol flows untouched, EXCEPT the NUT-05 melt-execute endpoint
(`POST /v1/melt/bolt11`): the first N executes after a reset are short-circuited
with the verbatim issue-#468 "not enough inputs" NUT-00 error instead of being
forwarded. The mint therefore never sees those melts (proofs stay unspent), so a
node that correctly retries can re-melt the same proofs and succeed.

This drives routstr-core's swap_to_primary_mint retry loop over the real wire:
real error string -> real cashu wallet lib -> the node's melt-failure classifier
-> retry. The melt-quote path (`POST /v1/melt/quote/bolt11`) is never faulted.

Control plane (not forwarded):
  POST /__proxy__/reset?faults=1&kind=melt_insufficient
  POST /__proxy__/reset?faults=1&kind=mint_quote_429&retry_after=0
  GET  /__proxy__/stats

Runs on the nutshell image (already ships fastapi/uvicorn/httpx) — no new build.
"""
import json
import os
from collections import Counter

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

TARGET = os.environ.get("PROXY_TARGET", "http://foreign-mint:3338").rstrip("/")
PORT = int(os.environ.get("PROXY_PORT", "3340"))

# NUT-05 melt EXECUTE paths (path param has no leading slash). The melt QUOTE
# path (v1/melt/quote/bolt11) is deliberately excluded — only the execute fails.
MELT_EXECUTE_PATHS = {"v1/melt/bolt11", "v1/melt"}
MINT_QUOTE_PATHS = {"v1/mint/quote/bolt11", "v1/mint/quote"}

# Raw NUT-00 error body a mint returns when inputs don't cover amount + fee.
# The node's classifier keys on the code (11000 = nutshell TransactionError) and
# the "not enough inputs" text; "Provided/needed" gives the retry shrink step.
_FAULT_BODY = json.dumps(
    {"detail": "not enough inputs provided for melt. Provided: 1, needed: 2", "code": 11000}
)

app = FastAPI()
_state = {
    "fault_remaining": 0,
    "fault_kind": "melt_insufficient",
    "retry_after": "0",
}
_stats: Counter = Counter()


@app.post("/__proxy__/reset")
async def reset(
    faults: int = 1,
    kind: str = "melt_insufficient",
    retry_after: str = "0",
) -> dict:
    if kind not in {"melt_insufficient", "mint_quote_429"}:
        return {"error": f"unknown fault kind: {kind}"}
    _state.update(
        fault_remaining=faults,
        fault_kind=kind,
        retry_after=retry_after,
    )
    _stats.clear()
    return dict(_state)


@app.get("/__proxy__/stats")
async def stats() -> dict:
    return {
        "melt_attempts": _stats["melt_attempts"],
        "mint_quote_attempts": _stats["mint_quote_attempts"],
        "forwarded": _stats["forwarded"],
        "faulted": _stats["faulted"],
        **_state,
    }


@app.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def proxy(path: str, request: Request) -> Response:
    body = await request.body()

    if request.method == "POST" and path in MELT_EXECUTE_PATHS:
        _stats["melt_attempts"] += 1
        print(f"[fault-proxy] melt execute #{_stats['melt_attempts']} "
              f"(fault_remaining={_state['fault_remaining']})", flush=True)
        if (
            _state["fault_kind"] == "melt_insufficient"
            and _state["fault_remaining"] > 0
        ):
            _state["fault_remaining"] -= 1
            _stats["faulted"] += 1
            return Response(
                content=_FAULT_BODY, status_code=400, media_type="application/json"
            )

    if request.method == "POST" and path in MINT_QUOTE_PATHS:
        _stats["mint_quote_attempts"] += 1
        if (
            _state["fault_kind"] == "mint_quote_429"
            and _state["fault_remaining"] > 0
        ):
            _state["fault_remaining"] -= 1
            _stats["faulted"] += 1
            return Response(
                content=json.dumps({"detail": "Rate limit exceeded."}),
                status_code=429,
                headers={"Retry-After": _state["retry_after"]},
                media_type="application/json",
            )

    _stats["forwarded"] += 1
    async with httpx.AsyncClient(timeout=90) as client:
        upstream = await client.request(
            request.method,
            f"{TARGET}/{path}",
            params=dict(request.query_params),
            content=body,
            headers={
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length")
            },
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
