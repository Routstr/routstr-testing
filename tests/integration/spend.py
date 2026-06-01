"""Spend reporting for paid integration tests.

Tests that move real ecash append the millisats they spent to the file named by
the ``SPEND_REPORT_PATH`` env var (one JSON object per line). The orchestrator
sums the file into a run's ``token_consumed_msats`` so the UI shows the true
spend — node billing is sub-sat, so whole-sat counters round it to 0.

Includes a tiny, dependency-free decoder for Cashu TokenV4 (``cashuB``) strings
so x-cashu tests can derive the amount spent as ``input - change`` without
pulling in a CBOR library.
"""
from __future__ import annotations

import base64
import json
import os


def _cbor(b: bytes, i: int):
    """Minimal CBOR decode — only the types a Cashu TokenV4 uses."""
    first = b[i]
    i += 1
    mt, ai = first >> 5, first & 0x1F
    if ai < 24:
        val = ai
    elif ai == 24:
        val, i = b[i], i + 1
    elif ai == 25:
        val, i = int.from_bytes(b[i : i + 2], "big"), i + 2
    elif ai == 26:
        val, i = int.from_bytes(b[i : i + 4], "big"), i + 4
    elif ai == 27:
        val, i = int.from_bytes(b[i : i + 8], "big"), i + 8
    else:
        raise ValueError(f"unsupported CBOR additional info {ai}")

    if mt == 0:  # unsigned int
        return val, i
    if mt == 2:  # byte string
        return b[i : i + val], i + val
    if mt == 3:  # text string
        return b[i : i + val].decode(), i + val
    if mt == 4:  # array
        out = []
        for _ in range(val):
            v, i = _cbor(b, i)
            out.append(v)
        return out, i
    if mt == 5:  # map
        out = {}
        for _ in range(val):
            k, i = _cbor(b, i)
            v, i = _cbor(b, i)
            out[k] = v
        return out, i
    raise ValueError(f"unsupported CBOR major type {mt}")


def token_amount_sats(token: str) -> int:
    """Sum the proof amounts (sats) in a ``cashuB`` TokenV4 string."""
    if not token or not token.startswith("cashuB"):
        raise ValueError("not a cashuB token")
    raw = token[6:]
    raw += "=" * (-len(raw) % 4)
    obj, _ = _cbor(base64.urlsafe_b64decode(raw), 0)
    return sum(p["a"] for entry in obj.get("t", []) for p in entry["p"])


def record_msats(msats: int) -> None:
    """Append a spend record (millisats) to ``$SPEND_REPORT_PATH`` if set."""
    path = os.environ.get("SPEND_REPORT_PATH")
    if not path or msats <= 0:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"msats": int(msats)}) + "\n")


def record_sats(sats: int) -> None:
    record_msats(int(sats) * 1000)
