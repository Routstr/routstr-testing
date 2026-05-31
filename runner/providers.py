"""Per-provider upstream profile registry (ROU-153).

A *upstream profile* selects which real LLM provider the in-compose
node-a/node-b talk to, parallel to ROU-151's `target_profile`. Adding a
provider is editing one YAML under `providers/`, not changing code:

    # providers/openai.yaml
    id: openai
    name: OpenAI
    upstream_base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    models_file: providers/models/openai.json
    required_env:
      - name: OPENAI_API_KEY
      - name: OPENROUTER_REFERER
        default: https://routstr-testing.local
    notes: |
      ...

The sentinel profile **`mock`** is not backed by a YAML — it is the current
default behaviour where node-a/node-b point at the in-compose `mock-openai`
container. `resolve_upstream_env("mock", ...)` returns an empty dict so
compose falls back to its baked-in `UPSTREAM_BASE_URL=http://mock-openai:3000`.

The models catalog is mounted read-only into the node containers at
`/providers-models` (see compose.yml); for a real profile the orchestrator
exports `UPSTREAM_MODELS_PATH=/providers-models/<provider>.json` which
routstr-core picks up via its `MODELS_PATH` env var.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# Sentinel profile: the in-compose mock-openai container (current default).
MOCK_PROFILE = "mock"

# Where the model catalogs are mounted inside the routstr node containers.
CONTAINER_MODELS_DIR = "/providers-models"


@dataclass
class RequiredEnv:
    name: str
    default: Optional[str] = None

    @property
    def is_secret(self) -> bool:
        """Heuristic: vars that look like keys/tokens are masked in the UI."""
        upper = self.name.upper()
        return any(tag in upper for tag in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


@dataclass
class Provider:
    id: str
    name: str
    upstream_base_url: str
    api_key_env: str
    models_file: str = ""
    required_env: list[RequiredEnv] = field(default_factory=list)
    notes: str = ""

    @property
    def required_env_names(self) -> list[str]:
        return [r.name for r in self.required_env]

    def missing_env(self, env: Optional[dict[str, str]] = None) -> list[str]:
        """Return required env var names that are unset and have no default."""
        source = os.environ if env is None else env
        missing: list[str] = []
        for item in self.required_env:
            if item.default is not None:
                continue
            value = source.get(item.name)
            if value is None or value == "":
                missing.append(item.name)
        return missing


def _coerce_required_env(raw: Any) -> list[RequiredEnv]:
    out: list[RequiredEnv] = []
    for item in raw or []:
        if isinstance(item, str):
            out.append(RequiredEnv(name=item))
        elif isinstance(item, dict):
            name = item.get("name")
            if not name:
                raise ValueError(f"required_env entry missing 'name': {item!r}")
            default = item.get("default")
            out.append(
                RequiredEnv(
                    name=str(name),
                    default=None if default is None else str(default),
                )
            )
        else:
            raise ValueError(f"invalid required_env entry: {item!r}")
    return out


def _provider_from_data(data: dict[str, Any], fallback_id: str) -> Provider:
    pid = str(data.get("id") or fallback_id)
    base_url = data.get("upstream_base_url")
    if not base_url:
        raise ValueError(f"provider {pid!r} is missing 'upstream_base_url'")
    api_key_env = data.get("api_key_env")
    if not api_key_env:
        raise ValueError(f"provider {pid!r} is missing 'api_key_env'")
    return Provider(
        id=pid,
        name=str(data.get("name", pid)),
        upstream_base_url=str(base_url),
        api_key_env=str(api_key_env),
        models_file=str(data.get("models_file", "")),
        required_env=_coerce_required_env(data.get("required_env")),
        notes=str(data.get("notes", "")),
    )


def load_provider(providers_dir: Path, profile_id: str) -> Provider:
    """Load one provider profile YAML.

    Raises FileNotFoundError if the profile has no YAML. The `mock` sentinel
    is handled by the caller (see `is_mock`); calling load_provider("mock")
    raises so callers must branch on the sentinel first.
    """
    if profile_id == MOCK_PROFILE:
        raise ValueError(
            "the 'mock' profile is the in-compose mock-openai container and has "
            "no provider YAML; branch on providers.is_mock() before loading."
        )
    candidate = providers_dir / f"{profile_id}.yaml"
    if not candidate.exists():
        candidate = providers_dir / f"{profile_id}.yml"
    if not candidate.exists():
        available = ", ".join(sorted(p.id for p in list_providers(providers_dir)))
        raise FileNotFoundError(
            f"upstream profile {profile_id!r} not found under {providers_dir} "
            f"(available: {available or 'none'}; or use 'mock')"
        )
    data = yaml.safe_load(candidate.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"provider YAML {candidate} must be a mapping")
    return _provider_from_data(data, candidate.stem)


def list_providers(providers_dir: Path) -> list[Provider]:
    """Load every provider YAML under `providers_dir`, sorted by id.

    Malformed YAMLs are skipped rather than crashing the listing (the UI
    dropdown should still populate from the valid ones).
    """
    if not providers_dir.exists():
        return []
    out: list[Provider] = []
    for path in sorted(providers_dir.glob("*.yaml")) + sorted(
        providers_dir.glob("*.yml")
    ):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            if isinstance(data, dict):
                out.append(_provider_from_data(data, path.stem))
        except (yaml.YAMLError, ValueError):
            continue
    # De-dup by id (a .yaml wins over a stray .yml of the same stem).
    seen: dict[str, Provider] = {}
    for p in out:
        seen.setdefault(p.id, p)
    return sorted(seen.values(), key=lambda p: p.id)


def is_mock(profile_id: Optional[str]) -> bool:
    return (profile_id or MOCK_PROFILE).strip().lower() == MOCK_PROFILE


def resolve_upstream_env(
    provider: Provider,
    *,
    env: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Resolve the compose env vars that point node-a/node-b at `provider`.

    Returns a dict suitable for merging into the orchestrator's environment
    before `docker compose up`. compose.yml references these with `${...}`
    defaults, so an empty dict (the `mock` case) leaves the baked-in
    mock-openai wiring untouched.

    The provider api key is read from `provider.api_key_env` in `env` (or the
    process environment) and surfaced as `UPSTREAM_API_KEY`. The key is never
    returned under its original name, so it cannot accidentally be persisted
    by callers that log the resolved dict by var name.
    """
    source = os.environ if env is None else env
    resolved: dict[str, str] = {
        "UPSTREAM_BASE_URL": provider.upstream_base_url,
        "UPSTREAM_API_KEY": source.get(provider.api_key_env, ""),
    }
    if provider.models_file:
        resolved["UPSTREAM_MODELS_PATH"] = (
            f"{CONTAINER_MODELS_DIR}/{Path(provider.models_file).name}"
        )
    # Apply defaults for any required_env that has one and isn't already set,
    # then pass through every required_env value so providers that need extra
    # vars (e.g. OPENROUTER_REFERER) reach the node containers.
    for item in provider.required_env:
        value = source.get(item.name)
        if (value is None or value == "") and item.default is not None:
            value = item.default
        if value:
            resolved[item.name] = value
    return resolved
