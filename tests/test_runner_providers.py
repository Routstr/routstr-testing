"""Unit tests for the upstream provider registry (ROU-153)."""

from __future__ import annotations

from pathlib import Path

import pytest

from runner import providers

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "providers"

SHIPPED = {"openai", "anthropic", "openrouter", "groq", "together", "fireworks"}


def test_all_shipped_providers_load():
    ids = {p.id for p in providers.list_providers(PROVIDERS_DIR)}
    assert SHIPPED <= ids, f"missing provider YAMLs: {SHIPPED - ids}"


def test_each_provider_has_a_models_catalog():
    for provider in providers.list_providers(PROVIDERS_DIR):
        catalog = REPO_ROOT / provider.models_file
        assert catalog.exists(), f"{provider.id} catalog missing: {catalog}"
        import json

        data = json.loads(catalog.read_text())
        assert 3 <= len(data["models"]) <= 5, (
            f"{provider.id} should ship 3-5 curated models"
        )


def test_is_mock_sentinel():
    assert providers.is_mock("mock")
    assert providers.is_mock(None)
    assert providers.is_mock("MOCK")
    assert not providers.is_mock("openai")


def test_load_mock_raises():
    with pytest.raises(ValueError):
        providers.load_provider(PROVIDERS_DIR, "mock")


def test_unknown_provider_lists_available():
    with pytest.raises(FileNotFoundError) as exc:
        providers.load_provider(PROVIDERS_DIR, "does-not-exist")
    # The error names the available providers so a typo is self-correcting.
    assert "openai" in str(exc.value)


def test_missing_env_reports_only_unset_no_default():
    p = providers.load_provider(PROVIDERS_DIR, "openrouter")
    # OPENROUTER_REFERER has a default → not "missing"; the key is.
    assert p.missing_env({}) == ["OPENROUTER_API_KEY"]
    assert p.missing_env({"OPENROUTER_API_KEY": "sk-x"}) == []


def test_resolve_upstream_env_maps_key_and_models():
    p = providers.load_provider(PROVIDERS_DIR, "openai")
    env = providers.resolve_upstream_env(p, env={"OPENAI_API_KEY": "sk-secret"})
    assert env["UPSTREAM_BASE_URL"] == "https://api.openai.com/v1"
    assert env["UPSTREAM_API_KEY"] == "sk-secret"
    assert env["UPSTREAM_MODELS_PATH"] == "/providers-models/openai.json"


def test_resolve_upstream_env_applies_required_env_default():
    p = providers.load_provider(PROVIDERS_DIR, "openrouter")
    env = providers.resolve_upstream_env(
        p, env={"OPENROUTER_API_KEY": "sk-x"}
    )
    # The referer default flows through to the node containers.
    assert env["OPENROUTER_REFERER"] == "https://routstr-testing.local"


def test_required_env_secret_heuristic():
    p = providers.load_provider(PROVIDERS_DIR, "openai")
    key_item = next(i for i in p.required_env if i.name == "OPENAI_API_KEY")
    assert key_item.is_secret
