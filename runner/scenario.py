"""Scenario YAML loading.

A scenario YAML looks like:

    id: golden_payment
    name: Golden payment round-trip
    description: ...
    target_profile: local        # local | remote — defaults to local
    selection:
      paths: [tests/e2e/test_golden.py]
      markers: [requires_funded_daemon]
    parameters:
      topup_sats: 1000
      model: gpt-3.5-turbo
    expected_cost_sats: 500
    timeout_seconds: 120

`target_profile: remote` skips the local compose bring-up. The orchestrator
expects `--remote-node-urls` (and optional `--remote-admin-tokens`) on the
command line, or `REMOTE_NODE_URLS` / `REMOTE_NODE_ADMIN_TOKEN_<i>` in env
when invoked by the FastAPI server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .providers import MOCK_PROFILE


TARGET_PROFILE_LOCAL = "local"
TARGET_PROFILE_REMOTE = "remote"
VALID_TARGET_PROFILES = (TARGET_PROFILE_LOCAL, TARGET_PROFILE_REMOTE)

# Upstream profile (ROU-153). `mock` is the in-compose mock-openai container
# (current default); any other value names a providers/*.yaml profile.
UPSTREAM_PROFILE_MOCK = MOCK_PROFILE


@dataclass
class Selection:
    paths: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def to_pytest_args(self) -> list[str]:
        args: list[str] = list(self.paths)
        for m in self.markers:
            args += ["-m", m]
        for k in self.keywords:
            args += ["-k", k]
        return args


@dataclass
class Scenario:
    id: str
    name: str
    description: str = ""
    selection: Selection = field(default_factory=Selection)
    parameters: dict[str, Any] = field(default_factory=dict)
    expected_cost_sats: int = 0
    timeout_seconds: int = 600
    services_required: bool = True
    target_profile: str = TARGET_PROFILE_LOCAL
    upstream_profile: str = UPSTREAM_PROFILE_MOCK
    estimated_upstream_cost_usd: float = 0.0
    required_env: list[str] = field(default_factory=list)
    raw_yaml: str = ""

    def env(self) -> dict[str, str]:
        """Render scenario parameters as environment variables for pytest.

        Each key in `parameters` becomes `SCENARIO_PARAM_<UPPER>=<value>`.
        Tests can read these to drive parametrized behavior. Also exports
        `TARGET_PROFILE` so the tests/conftest.py auto-skip logic and
        target-aware fixtures see the resolved profile.
        """
        out: dict[str, str] = {
            "SCENARIO_ID": self.id,
            "SCENARIO_EXPECTED_COST_SATS": str(self.expected_cost_sats),
            "TARGET_PROFILE": self.target_profile,
            "UPSTREAM_PROFILE": self.upstream_profile,
        }
        for key, value in self.parameters.items():
            out[f"SCENARIO_PARAM_{key.upper()}"] = str(value)
        return out

    @property
    def is_remote(self) -> bool:
        return self.target_profile == TARGET_PROFILE_REMOTE

    @property
    def is_mock_upstream(self) -> bool:
        return self.upstream_profile == UPSTREAM_PROFILE_MOCK


def _coerce_target_profile(raw: Any) -> str:
    if raw is None:
        return TARGET_PROFILE_LOCAL
    value = str(raw).strip().lower()
    if value not in VALID_TARGET_PROFILES:
        raise ValueError(
            f"invalid target_profile {raw!r}; expected one of "
            f"{VALID_TARGET_PROFILES}"
        )
    return value


def _coerce_upstream_profile(raw: Any) -> str:
    """Normalise the upstream profile name.

    Existence of a non-`mock` profile is validated lazily by the orchestrator
    against the providers/ directory (so a typo surfaces with the list of
    available providers), not here at YAML-load time.
    """
    if raw is None:
        return UPSTREAM_PROFILE_MOCK
    value = str(raw).strip().lower()
    return value or UPSTREAM_PROFILE_MOCK


def load_scenario(scenarios_dir: Path, scenario_id: str) -> Scenario:
    candidate = scenarios_dir / f"{scenario_id}.yaml"
    if not candidate.exists():
        candidate = scenarios_dir / f"{scenario_id}.yml"
    if not candidate.exists():
        raise FileNotFoundError(
            f"scenario {scenario_id!r} not found under {scenarios_dir}"
        )

    raw_yaml = candidate.read_text()
    data = yaml.safe_load(raw_yaml) or {}

    sel = data.get("selection") or {}
    selection = Selection(
        paths=list(sel.get("paths", [])),
        markers=list(sel.get("markers", [])),
        keywords=list(sel.get("keywords", [])),
    )

    target_profile = _coerce_target_profile(data.get("target_profile"))
    upstream_profile = _coerce_upstream_profile(data.get("upstream_profile"))

    raw_required = data.get("required_env") or []
    required_env = [str(item) for item in raw_required]

    try:
        estimated_cost = float(data.get("estimated_upstream_cost_usd", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"estimated_upstream_cost_usd in scenario {scenario_id!r} must be a "
            f"number, got {data.get('estimated_upstream_cost_usd')!r}"
        ) from exc

    return Scenario(
        id=data.get("id", scenario_id),
        name=data.get("name", scenario_id),
        description=data.get("description", ""),
        selection=selection,
        parameters=dict(data.get("parameters", {})),
        expected_cost_sats=int(data.get("expected_cost_sats", 0)),
        timeout_seconds=int(data.get("timeout_seconds", 600)),
        services_required=bool(data.get("services_required", True)),
        target_profile=target_profile,
        upstream_profile=upstream_profile,
        estimated_upstream_cost_usd=estimated_cost,
        required_env=required_env,
        raw_yaml=raw_yaml,
    )
