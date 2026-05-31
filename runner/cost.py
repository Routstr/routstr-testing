"""Best-effort upstream cost telemetry (ROU-153).

`upstream_actual_cost_usd` is priced from the per-provider model catalog
(`providers/models/<provider>.json`, OpenRouter-shaped per-token USD) applied
to whatever token usage a real_upstream test reports.

Contract: a real_upstream test that wants its spend priced writes one JSON
object per upstream call to `<artifacts_dir>/upstream_usage.jsonl`:

    {"model": "gpt-4o-mini", "prompt_tokens": 12, "completion_tokens": 34}

The orchestrator reads that file after pytest, looks each model up in the
active provider catalog, and sums `prompt_tokens * prompt + completion_tokens
* completion`. This is intentionally best-effort:

  * If the file is absent (no test reported usage) → returns None.
  * If a model isn't in the catalog → that line contributes 0 and is counted
    in `unpriced`.
  * Streamed responses and providers that omit `usage` simply don't write the
    file, so the actual cost stays None rather than wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

USAGE_FILENAME = "upstream_usage.jsonl"


def load_model_pricing(models_file: Path) -> dict[str, tuple[float, float]]:
    """Map model id → (prompt_usd_per_token, completion_usd_per_token)."""
    if not models_file.exists():
        return {}
    try:
        data = json.loads(models_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    pricing: dict[str, tuple[float, float]] = {}
    for entry in data.get("models", []):
        mid = entry.get("id")
        price = entry.get("pricing") or {}
        if not mid:
            continue
        try:
            prompt = float(price.get("prompt", 0) or 0)
            completion = float(price.get("completion", 0) or 0)
        except (TypeError, ValueError):
            continue
        pricing[str(mid)] = (prompt, completion)
    return pricing


@dataclass
class PricedUsage:
    total_usd: float
    calls: int
    unpriced: int  # calls whose model wasn't in the catalog


def price_usage_file(
    usage_path: Path, models_file: Path
) -> Optional[PricedUsage]:
    """Price the usage JSONL against the model catalog.

    Returns None when the usage file is absent (no usable usage was reported)
    so the caller records `upstream_actual_cost_usd = NULL` rather than a
    misleading 0.0.
    """
    if not usage_path.exists():
        return None
    pricing = load_model_pricing(models_file)
    total = 0.0
    calls = 0
    unpriced = 0
    for line in usage_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        calls += 1
        model = str(record.get("model", ""))
        prompt_tokens = int(record.get("prompt_tokens", 0) or 0)
        completion_tokens = int(record.get("completion_tokens", 0) or 0)
        rate = pricing.get(model)
        if rate is None:
            unpriced += 1
            continue
        total += prompt_tokens * rate[0] + completion_tokens * rate[1]
    if calls == 0:
        return None
    return PricedUsage(total_usd=total, calls=calls, unpriced=unpriced)
