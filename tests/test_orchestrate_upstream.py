"""Orchestrator upstream-profile resolution + cost telemetry (ROU-153).

These exercise the validation/cost logic directly (no docker / no real
provider) so the matrix rules and cost gate are provably enforced before any
stack bring-up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.cost import PricedUsage, USAGE_FILENAME, price_usage_file
from runner.orchestrate import UpstreamConfigError, _resolve_upstream
from runner.scenario import Scenario, Selection

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "providers"


def _scenario(**kw) -> Scenario:
    base = dict(id="s", name="s")
    base.update(kw)
    return Scenario(**base)


def _resolve(scenario, *, is_remote=False, env=None, max_usd=1.00):
    return _resolve_upstream(
        scenario,
        is_remote=is_remote,
        providers_dir=PROVIDERS_DIR,
        upstream_env=env,
        upstream_max_usd=max_usd,
    )


# ── mock (default) ────────────────────────────────────────────────────────


def test_mock_profile_is_noop():
    plan = _resolve(_scenario(upstream_profile="mock"))
    assert plan.is_mock
    assert plan.compose_env == {}
    assert plan.estimated_cost_usd == 0.0


# ── local + real_upstream ─────────────────────────────────────────────────


def test_local_real_requires_api_key():
    sc = _scenario(upstream_profile="openai", estimated_upstream_cost_usd=0.01)
    with pytest.raises(UpstreamConfigError) as exc:
        _resolve(sc, env={})  # no OPENAI_API_KEY
    assert "OPENAI_API_KEY" in str(exc.value)


def test_local_real_injects_provider_env():
    sc = _scenario(upstream_profile="openai", estimated_upstream_cost_usd=0.01)
    plan = _resolve(sc, env={"OPENAI_API_KEY": "sk-test"})
    assert not plan.is_mock
    assert plan.profile == "openai"
    assert plan.compose_env["UPSTREAM_BASE_URL"] == "https://api.openai.com/v1"
    assert plan.compose_env["UPSTREAM_API_KEY"] == "sk-test"
    assert plan.models_file is not None and plan.models_file.exists()


def test_cost_gate_blocks_over_budget():
    sc = _scenario(upstream_profile="openai", estimated_upstream_cost_usd=0.01)
    with pytest.raises(UpstreamConfigError) as exc:
        _resolve(sc, env={"OPENAI_API_KEY": "sk-test"}, max_usd=0.001)
    assert "exceeds" in str(exc.value).lower()


def test_unknown_profile_errors_with_available_list():
    sc = _scenario(upstream_profile="nope", estimated_upstream_cost_usd=0.0)
    with pytest.raises(UpstreamConfigError) as exc:
        _resolve(sc, env={})
    assert "openai" in str(exc.value)


# ── remote matrix ─────────────────────────────────────────────────────────


def test_remote_plus_mock_plus_real_marker_is_invalid():
    sc = _scenario(
        upstream_profile="mock",
        selection=Selection(markers=["real_upstream"]),
    )
    with pytest.raises(UpstreamConfigError) as exc:
        _resolve(sc, is_remote=True)
    assert "remote" in str(exc.value).lower()


def test_remote_plus_mock_readonly_is_allowed():
    # ROU-151's remote_smoke flow: remote + mock without real_upstream tests.
    sc = _scenario(
        upstream_profile="mock", selection=Selection(markers=["safe_for_remote"])
    )
    plan = _resolve(sc, is_remote=True)
    assert plan.is_mock


def test_remote_plus_real_no_provider_key_needed():
    # The harness doesn't configure a remote node's upstream → no key required,
    # but the cost gate + label still apply.
    sc = _scenario(upstream_profile="openai", estimated_upstream_cost_usd=0.02)
    plan = _resolve(sc, is_remote=True, env={})
    assert plan.profile == "openai"
    assert plan.compose_env == {}  # nothing injected for remote
    assert plan.estimated_cost_usd == 0.02


# ── cost telemetry ────────────────────────────────────────────────────────


def test_price_usage_absent_is_none(tmp_path: Path):
    models = PROVIDERS_DIR / "providers" / "models"  # wrong path on purpose
    assert price_usage_file(tmp_path / USAGE_FILENAME, tmp_path / "x.json") is None


def test_price_usage_from_catalog(tmp_path: Path):
    usage = tmp_path / USAGE_FILENAME
    usage.write_text(
        json.dumps(
            {"model": "gpt-4o-mini", "prompt_tokens": 1000, "completion_tokens": 1000}
        )
        + "\n"
    )
    priced = price_usage_file(usage, PROVIDERS_DIR / "models" / "openai.json")
    assert isinstance(priced, PricedUsage)
    assert priced.calls == 1
    # gpt-4o-mini: 0.00000015 prompt + 0.0000006 completion per token.
    assert priced.total_usd == pytest.approx(1000 * 0.00000015 + 1000 * 0.0000006)


def test_price_usage_unknown_model_counted_unpriced(tmp_path: Path):
    usage = tmp_path / USAGE_FILENAME
    usage.write_text(
        json.dumps({"model": "made-up", "prompt_tokens": 10, "completion_tokens": 5})
        + "\n"
    )
    priced = price_usage_file(usage, PROVIDERS_DIR / "models" / "openai.json")
    assert priced.calls == 1 and priced.unpriced == 1 and priced.total_usd == 0.0
