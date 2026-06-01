"""Unit tests for spend reporting — no stack or funds required."""
from __future__ import annotations

from pathlib import Path

from runner.orchestrate import _sum_spend_msats
from tests.integration import spend

# A synthetic Cashu TokenV4 with proof amounts [4, 1] (sum 5). The C points are
# zeroed — token_amount_sats only reads the amount fields, not the signatures.
SYNTHETIC_TOKEN = (
    "cashuBo2FtdGh0dHBzOi8vbWludC5leGFtcGxlYXVjc2F0YXSBomFpSACqu8zd7v8AYXCC"
    "o2FhBGFzYnMxYWNYIQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAKNhYQFhc2"
    "JzMmFjWCEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def test_token_amount_sats_sums_proofs():
    assert spend.token_amount_sats(SYNTHETIC_TOKEN) == 5


def test_record_and_sum_roundtrip(tmp_path: Path, monkeypatch):
    report = tmp_path / "spend.jsonl"
    monkeypatch.setenv("SPEND_REPORT_PATH", str(report))
    spend.record_msats(150)
    spend.record_sats(2)  # -> 2000 msat
    spend.record_msats(0)  # ignored
    assert _sum_spend_msats(report) == 2150


def test_sum_missing_file_is_zero(tmp_path: Path):
    assert _sum_spend_msats(tmp_path / "nope.jsonl") == 0


def test_record_noop_without_env(monkeypatch):
    monkeypatch.delenv("SPEND_REPORT_PATH", raising=False)
    spend.record_msats(999)  # must not raise
