"""Scenario-driven orchestrator for the Routstr test harness.

Acceptance from ROU-133:
  python runner/orchestrate.py --scenario smoke --token <cashu>
  → produces a `runs` row in runs.db with linked `test_results`,
    and captures resolved vendor shas in `runs.vendor_commits_json`.

Pipeline:
  1. scripts/sync.sh (unless SKIP_SYNC=1)
  2. resolve scenario YAML → pytest args + env
  3. compose up + wait_for + topup routstrd (best-effort; missing scripts
     yield warnings, not crashes — the orchestrator's job is the pipeline)
  4. pytest with --junitxml
  5. parse junit, write rows to runs.db
  6. on failure, dump per-service logs into logs/<service>.log
  7. compose down (unless KEEP_UP=1)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import select

from . import providers as provider_registry
from .balance import fetch_total_sats as fetch_routstrd_total_sats
from .compose import dump_logs as compose_dump_logs
from .compose import down as compose_down
from .compose import up as compose_up
from .cost import USAGE_FILENAME, price_usage_file
from .junit import parse_junit
from .models import Run, Scenario as ScenarioRow, TestResult, get_engine, get_session
from .scenario import (
    TARGET_PROFILE_LOCAL,
    TARGET_PROFILE_REMOTE,
    Scenario,
    load_scenario,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "runs.db"
DEFAULT_LOGS = REPO_ROOT / "logs"
DEFAULT_COMPOSE = REPO_ROOT / "compose.yml"
DEFAULT_SCENARIOS = REPO_ROOT / "scenarios"
DEFAULT_PROVIDERS = REPO_ROOT / "providers"

# ROU-153 cost ceiling. The orchestrator refuses to start a real-upstream run
# whose summed estimated cost exceeds this (raise UPSTREAM_MAX_USD per run).
DEFAULT_UPSTREAM_MAX_USD = 1.00

REAL_UPSTREAM_MARKER = "real_upstream"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync.sh"
TOPUP_SCRIPT = REPO_ROOT / "scripts" / "topup_routstrd.sh"
WAIT_FOR_SCRIPT = REPO_ROOT / "scripts" / "wait_for.sh"

# Tests that move real ecash append per-request spend (millisats) here; the
# orchestrator sums it into the run's token_consumed_msats. Node billing is
# sub-sat, so this is the only signal that reflects tiny real spends.
SPEND_FILENAME = "spend.jsonl"


def _sum_spend_msats(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            total += int(json.loads(line).get("msats", 0))
        except (ValueError, TypeError):
            continue
    return total
COMMITS_FILE = REPO_ROOT / "vendor" / "COMMITS.txt"


def _log(msg: str) -> None:
    print(f"[orchestrate] {msg}", flush=True)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


def _run_sync() -> tuple[bool, str]:
    if _env_flag("SKIP_SYNC"):
        _log("SKIP_SYNC=1 → skipping scripts/sync.sh")
        return True, ""
    if not SYNC_SCRIPT.exists():
        return False, f"sync script missing: {SYNC_SCRIPT}"
    proc = subprocess.run(
        ["bash", str(SYNC_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _read_vendor_commits() -> dict[str, str]:
    if not COMMITS_FILE.exists():
        return {}
    commits: dict[str, str] = {}
    for line in COMMITS_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            commits[parts[0]] = parts[1]
    return commits


def _wait_for_services() -> tuple[bool, str]:
    """Optional readiness wait — runs scripts/wait_for.sh if present."""
    if not WAIT_FOR_SCRIPT.exists():
        return True, "wait_for.sh not present; skipping readiness probe"
    proc = subprocess.run(
        ["bash", str(WAIT_FOR_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _topup(token: str) -> tuple[bool, str]:
    """Topup routstrd with the provided cashu token."""
    if not TOPUP_SCRIPT.exists():
        return False, (
            f"topup script missing: {TOPUP_SCRIPT}. Skipped topup; tests "
            "requiring funded_daemon will be flagged."
        )
    proc = subprocess.run(
        ["bash", str(TOPUP_SCRIPT), token],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _build_pytest_cmd(
    scenario: Scenario, junit_path: Path
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        f"--junitxml={junit_path}",
    ]
    cmd += scenario.selection.to_pytest_args()
    return cmd


def _run_pytest(
    scenario: Scenario, junit_path: Path, scenario_env: dict[str, str]
) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(scenario_env)
    cmd = _build_pytest_cmd(scenario, junit_path)
    _log("pytest: " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=scenario.timeout_seconds,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _ensure_scenario_row(engine, scenario: Scenario) -> None:
    with get_session(engine) as session:
        existing = session.exec(
            select(ScenarioRow).where(ScenarioRow.id == scenario.id)
        ).first()
        if existing is None:
            session.add(
                ScenarioRow(
                    id=scenario.id,
                    name=scenario.name,
                    description=scenario.description,
                    yaml=scenario.raw_yaml,
                    updated_at=datetime.utcnow(),
                )
            )
        else:
            existing.name = scenario.name
            existing.description = scenario.description
            existing.yaml = scenario.raw_yaml
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        session.commit()


def _insert_run(
    engine,
    *,
    scenario_id: str,
    artifacts_dir: Path,
    target_profile: str = TARGET_PROFILE_LOCAL,
    remote_node_urls: list[str] | None = None,
    upstream_profile: str = provider_registry.MOCK_PROFILE,
    upstream_estimated_cost_usd: float | None = None,
) -> int:
    with get_session(engine) as session:
        row = Run(
            scenario_id=scenario_id,
            started_at=datetime.utcnow(),
            status="running",
            artifacts_dir=str(artifacts_dir),
            target_profile=target_profile,
            remote_node_urls_json=(
                json.dumps(remote_node_urls) if remote_node_urls else None
            ),
            upstream_profile=upstream_profile,
            upstream_estimated_cost_usd=upstream_estimated_cost_usd,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None
        return row.id


def _finalize_run(
    engine,
    *,
    run_id: int,
    status: str,
    vendor_commits: dict[str, str],
    error_message: str | None = None,
    token_consumed_sats: int = 0,
    token_consumed_msats: int = 0,
    upstream_actual_cost_usd: float | None = None,
) -> None:
    with get_session(engine) as session:
        row = session.get(Run, run_id)
        if row is None:
            return
        row.status = status
        row.finished_at = datetime.utcnow()
        row.vendor_commits_json = json.dumps(vendor_commits)
        row.error_message = error_message
        row.token_consumed_sats = token_consumed_sats
        row.token_consumed_msats = token_consumed_msats
        if upstream_actual_cost_usd is not None:
            row.upstream_actual_cost_usd = upstream_actual_cost_usd
        session.add(row)
        session.commit()


def _insert_test_results(engine, run_id: int, parsed) -> None:
    if not parsed:
        return
    with get_session(engine) as session:
        for p in parsed:
            session.add(
                TestResult(
                    run_id=run_id,
                    test_name=p.test_name,
                    outcome=p.outcome,
                    duration_ms=p.duration_ms,
                    error_excerpt=p.error_excerpt,
                )
            )
        session.commit()


def _new_artifacts_dir() -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = DEFAULT_LOGS / stamp
    target.mkdir(parents=True, exist_ok=True)
    return target


def _normalise_remote_url(url: str) -> str:
    """`routstrd`'s seed-providers expects scheme + trailing slash."""
    return url.rstrip("/") + "/"


class UpstreamConfigError(ValueError):
    """Raised for an invalid upstream-profile configuration.

    Surfaced before any stack bring-up so a missing key or over-budget run
    fails fast with a clear message instead of charging a real provider.
    """


@dataclass
class _UpstreamPlan:
    profile: str
    is_mock: bool
    estimated_cost_usd: float
    compose_env: dict[str, str]  # injected into compose (UPSTREAM_BASE_URL, ...)
    models_file: Path | None = None  # host catalog used to price actual usage


def _resolve_upstream(
    scenario: Scenario,
    *,
    is_remote: bool,
    providers_dir: Path,
    upstream_env: dict[str, str] | None,
    upstream_max_usd: float,
) -> _UpstreamPlan:
    """Validate the upstream profile and resolve the env compose needs.

    Enforces the ROU-153 matrix (see issue table):
      local  + mock          → default; no provider env, no cost gate.
      local  + real_upstream → inject provider env; require api key + cost gate.
      remote + mock          → only invalid when the scenario selects the
                               `real_upstream` marker (a real-cost test against
                               a node the harness can't point at a real
                               upstream); otherwise it is ROU-151's read-only
                               remote flow and stays valid.
      remote + real_upstream → label + cost gate only; the harness does not
                               configure someone else's node, so no provider
                               key is required and no env is injected.
    """
    profile = (scenario.upstream_profile or provider_registry.MOCK_PROFILE).lower()
    is_mock = provider_registry.is_mock(profile)
    overlay = {**os.environ, **(upstream_env or {})}
    selects_real = REAL_UPSTREAM_MARKER in scenario.selection.markers

    if is_mock:
        if is_remote and selects_real:
            raise UpstreamConfigError(
                "invalid profile combo: target_profile=remote with "
                "upstream_profile=mock cannot run real_upstream tests — a "
                "remote node's upstream is its operator's, not the harness's. "
                "Set upstream_profile to a real provider (e.g. openai)."
            )
        return _UpstreamPlan(
            profile=provider_registry.MOCK_PROFILE,
            is_mock=True,
            estimated_cost_usd=0.0,
            compose_env={},
        )

    # Real upstream: cost gate applies in both local and remote modes.
    estimated = float(scenario.estimated_upstream_cost_usd or 0.0)
    if estimated > upstream_max_usd:
        raise UpstreamConfigError(
            f"estimated upstream cost ${estimated:.4f} exceeds UPSTREAM_MAX_USD "
            f"${upstream_max_usd:.4f} for scenario {scenario.id!r}. Raise "
            f"UPSTREAM_MAX_USD to run anyway."
        )

    try:
        provider = provider_registry.load_provider(providers_dir, profile)
    except (FileNotFoundError, ValueError) as exc:
        raise UpstreamConfigError(str(exc)) from exc

    # Scenario-declared required_env (beyond the provider's own) must be set.
    scenario_missing = [
        name
        for name in scenario.required_env
        if not overlay.get(name)
    ]

    if is_remote:
        # Harness doesn't configure the remote node, so we don't need the
        # provider api key; only scenario-level required_env is enforced.
        if scenario_missing:
            raise UpstreamConfigError(
                "missing required env var(s) for scenario "
                f"{scenario.id!r}: {', '.join(scenario_missing)}"
            )
        return _UpstreamPlan(
            profile=provider.id,
            is_mock=False,
            estimated_cost_usd=estimated,
            compose_env={},
            models_file=_provider_models_path(providers_dir, provider),
        )

    # local + real_upstream: the harness wires node-a/node-b to the provider,
    # so the provider api key (and any required_env) MUST be present.
    missing = provider.missing_env(overlay) + scenario_missing
    if missing:
        raise UpstreamConfigError(
            f"missing required env var(s) for upstream profile {provider.id!r}: "
            f"{', '.join(dict.fromkeys(missing))}. Set them before starting the "
            "stack (keys are passed through to compose, never persisted)."
        )
    compose_env = provider_registry.resolve_upstream_env(provider, env=overlay)
    return _UpstreamPlan(
        profile=provider.id,
        is_mock=False,
        estimated_cost_usd=estimated,
        compose_env=compose_env,
        models_file=_provider_models_path(providers_dir, provider),
    )


def _provider_models_path(
    providers_dir: Path, provider: provider_registry.Provider
) -> Path | None:
    """Host path to the provider's model catalog (for pricing actual usage)."""
    if not provider.models_file:
        return None
    candidate = Path(provider.models_file)
    if candidate.is_absolute():
        return candidate
    # models_file is repo-relative (e.g. providers/models/openai.json); resolve
    # it against the repo root, falling back to providers_dir's parent.
    return (providers_dir.parent / provider.models_file).resolve()


def orchestrate(
    *,
    scenario_id: str,
    token: str | None,
    db_path: Path = DEFAULT_DB,
    scenarios_dir: Path = DEFAULT_SCENARIOS,
    compose_file: Path = DEFAULT_COMPOSE,
    providers_dir: Path = DEFAULT_PROVIDERS,
    target_profile_override: str | None = None,
    upstream_profile_override: str | None = None,
    remote_node_urls: list[str] | None = None,
    remote_admin_tokens: list[str] | None = None,
    upstream_env: dict[str, str] | None = None,
    upstream_max_usd: float | None = None,
) -> int:
    """Run the orchestrator for one scenario.

    Returns the run id (rowid) inserted into the runs table.

    `target_profile_override` (when set) overrides the scenario YAML's
    `target_profile` field. `remote_node_urls` must be provided when the
    effective profile is `remote`. `remote_admin_tokens` is positional —
    `remote_admin_tokens[i]` becomes `REMOTE_NODE_ADMIN_TOKEN_<i>` in the
    pytest env. Tokens are never persisted to runs.db.
    """
    engine = get_engine(db_path)
    scenario = load_scenario(scenarios_dir, scenario_id)
    if target_profile_override:
        scenario.target_profile = target_profile_override
    if upstream_profile_override:
        scenario.upstream_profile = upstream_profile_override.strip().lower()

    if upstream_max_usd is None:
        try:
            upstream_max_usd = float(
                os.environ.get("UPSTREAM_MAX_USD", DEFAULT_UPSTREAM_MAX_USD)
            )
        except ValueError:
            upstream_max_usd = DEFAULT_UPSTREAM_MAX_USD

    is_remote = scenario.is_remote

    # Validate + resolve the upstream profile BEFORE any stack bring-up so a
    # missing key / over-budget run fails fast without charging a provider.
    upstream_plan = _resolve_upstream(
        scenario,
        is_remote=is_remote,
        providers_dir=providers_dir,
        upstream_env=upstream_env,
        upstream_max_usd=upstream_max_usd,
    )
    if not upstream_plan.is_mock:
        _log(
            f"upstream profile: {upstream_plan.profile} "
            f"(estimated ${upstream_plan.estimated_cost_usd:.4f}, "
            f"cap ${upstream_max_usd:.2f})"
        )
        # Inject resolved provider env so `docker compose up` (which inherits
        # this process's environment) points node-a/node-b at the real
        # provider. Keys live only in os.environ for this process — never
        # persisted to runs.db.
        for key, value in upstream_plan.compose_env.items():
            os.environ[key] = value

    if is_remote:
        if not remote_node_urls:
            raise ValueError(
                "target_profile=remote requires at least one --remote-node-url "
                "(or REMOTE_NODE_URLS env var) — refusing to spin up the local "
                "compose stack when the scenario asks for a remote profile."
            )
        remote_node_urls = [_normalise_remote_url(u) for u in remote_node_urls]
        _log(
            f"remote profile: {len(remote_node_urls)} node URL(s) — "
            f"{', '.join(remote_node_urls)}"
        )
        if remote_admin_tokens:
            _log(
                f"remote profile: {len(remote_admin_tokens)} admin token(s) "
                "(env-only, not persisted)"
            )
    _ensure_scenario_row(engine, scenario)

    artifacts_dir = _new_artifacts_dir()
    run_id = _insert_run(
        engine,
        scenario_id=scenario.id,
        artifacts_dir=artifacts_dir,
        target_profile=scenario.target_profile,
        remote_node_urls=remote_node_urls if is_remote else None,
        upstream_profile=upstream_plan.profile,
        upstream_estimated_cost_usd=(
            None if upstream_plan.is_mock else upstream_plan.estimated_cost_usd
        ),
    )
    _log(
        f"run #{run_id} → artifacts at {artifacts_dir} "
        f"(target_profile={scenario.target_profile})"
    )

    overall_status = "passed"
    error_message: str | None = None
    teardown_logs_needed = False
    balance_before: int | None = None
    balance_after: int | None = None
    upstream_actual_cost: float | None = None

    try:
        ok, output = _run_sync()
        (artifacts_dir / "sync.log").write_text(output or "")
        if not ok:
            overall_status = "error"
            error_message = "sync.sh failed"
            return run_id

        # Compose is only brought up for `local` profile. In `remote` mode the
        # nodes are someone else's deployment; we never touch their infra.
        compose_used = (
            (not is_remote)
            and scenario.services_required
            and compose_file.exists()
            and shutil.which("docker") is not None
        )
        if compose_used:
            up_ok, up_out = compose_up(compose_file, project_dir=REPO_ROOT)
            (artifacts_dir / "compose-up.log").write_text(up_out)
            if not up_ok:
                overall_status = "error"
                error_message = "docker compose up failed"
                teardown_logs_needed = True
                return run_id

            wait_ok, wait_out = _wait_for_services()
            (artifacts_dir / "wait_for.log").write_text(wait_out)
            if not wait_ok:
                overall_status = "error"
                error_message = "readiness probe failed"
                teardown_logs_needed = True
                return run_id
        else:
            reason = "target_profile=remote" if is_remote else (
                f"services_required={scenario.services_required}"
            )
            _log(f"compose bring-up skipped ({reason})")

        # Topup still applies in remote profile — `routstrd` is local and
        # talks to the remote nodes, so its balance still needs funding for
        # any tests that exercise paid flows.
        token_ok = True
        if token and scenario.services_required:
            topup_ok, topup_out = _topup(token)
            (artifacts_dir / "topup.log").write_text(topup_out)
            if not topup_ok:
                token_ok = False
                _log("topup failed — continuing; funded tests will be flagged")

        if scenario.services_required and not is_remote:
            # routstrd balance probe is only meaningful when we have a local
            # daemon to probe. In remote mode, a follow-up can wire up a
            # remote balance source.
            balance_before = fetch_routstrd_total_sats()
            _log(f"routstrd balance before pytest: {balance_before}")

        junit_path = artifacts_dir / "junit.xml"
        scenario_env = scenario.env()
        if is_remote and remote_node_urls:
            scenario_env["REMOTE_NODE_URLS"] = ",".join(remote_node_urls)
            scenario_env["ROUTSTRD_BOOTSTRAP_PROVIDERS"] = ",".join(
                remote_node_urls
            )
        for idx, admin_token in enumerate(remote_admin_tokens or []):
            scenario_env[f"REMOTE_NODE_ADMIN_TOKEN_{idx}"] = admin_token
        if not token_ok:
            scenario_env["TOPUP_FAILED"] = "1"
        # Paid tests append their precise spend (millisats) here; summed below
        # into token_consumed_msats so the UI shows sub-sat spend correctly.
        scenario_env["SPEND_REPORT_PATH"] = str(artifacts_dir / SPEND_FILENAME)
        # ROU-153: real_upstream tests append per-call usage here so the
        # orchestrator can price actual spend from the provider catalog.
        if not upstream_plan.is_mock:
            scenario_env["UPSTREAM_USAGE_PATH"] = str(
                artifacts_dir / USAGE_FILENAME
            )

        try:
            rc, output = _run_pytest(scenario, junit_path, scenario_env)
        except subprocess.TimeoutExpired as exc:
            (artifacts_dir / "pytest.log").write_text(
                f"timeout after {scenario.timeout_seconds}s\n{exc}"
            )
            if scenario.services_required:
                balance_after = fetch_routstrd_total_sats()
            overall_status = "error"
            error_message = f"pytest timed out after {scenario.timeout_seconds}s"
            teardown_logs_needed = True
            return run_id

        (artifacts_dir / "pytest.log").write_text(output)

        if scenario.services_required and not is_remote:
            balance_after = fetch_routstrd_total_sats()
            _log(f"routstrd balance after pytest: {balance_after}")

        parsed = parse_junit(junit_path)
        _insert_test_results(engine, run_id, parsed)

        # Best-effort actual upstream spend: price any usage a real_upstream
        # test reported (artifacts/upstream_usage.jsonl) against the provider
        # catalog. Stays None for mock or when no usage was reported.
        if not upstream_plan.is_mock and upstream_plan.models_file is not None:
            priced = price_usage_file(
                artifacts_dir / USAGE_FILENAME, upstream_plan.models_file
            )
            if priced is not None:
                upstream_actual_cost = priced.total_usd
                _log(
                    f"upstream actual cost: ${priced.total_usd:.6f} over "
                    f"{priced.calls} call(s) ({priced.unpriced} unpriced)"
                )

        if rc == 5:  # pytest "no tests collected"
            overall_status = "error"
            error_message = "no tests collected for selection"
        elif rc != 0:
            overall_status = "failed"
            teardown_logs_needed = True

    except Exception as exc:  # safety net so we always finalize the row
        overall_status = "error"
        error_message = f"orchestrator crashed: {exc!r}"
        _log(error_message)
    finally:
        vendor_commits = _read_vendor_commits()

        compose_active = (
            (not is_remote)
            and scenario.services_required
            and compose_file.exists()
            and shutil.which("docker") is not None
        )
        if teardown_logs_needed and compose_active:
            try:
                compose_dump_logs(
                    compose_file,
                    project_dir=REPO_ROOT,
                    logs_dir=artifacts_dir,
                )
            except Exception as exc:  # don't let log dumping mask the failure
                _log(f"log dump failed: {exc!r}")

        if not _env_flag("KEEP_UP") and compose_active:
            try:
                compose_down(compose_file, project_dir=REPO_ROOT)
            except Exception as exc:
                _log(f"compose down failed: {exc!r}")

        # Precise per-request spend reported by paid tests (millisats).
        token_consumed_msats = _sum_spend_msats(artifacts_dir / SPEND_FILENAME)

        token_consumed_sats = 0
        if balance_before is not None and balance_after is not None:
            token_consumed_sats = max(0, balance_before - balance_after)
        # Fall back to the reported spend when the routstrd balance probe was
        # skipped (services_required=false) or didn't move (tests pay a node
        # directly), so the UI reflects the real spend instead of 0.
        if token_consumed_sats == 0:
            token_consumed_sats = token_consumed_msats // 1000

        _finalize_run(
            engine,
            run_id=run_id,
            status=overall_status,
            vendor_commits=vendor_commits,
            error_message=error_message,
            token_consumed_sats=token_consumed_sats,
            token_consumed_msats=token_consumed_msats,
            upstream_actual_cost_usd=upstream_actual_cost,
        )
        _log(
            f"run #{run_id} status={overall_status} "
            f"consumed_sats={token_consumed_sats} "
            f"consumed_msats={token_consumed_msats} "
            f"upstream={upstream_plan.profile} "
            f"commits={list(vendor_commits)}"
        )

    return run_id


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrate")
    parser.add_argument("--scenario", required=True, help="Scenario id (YAML stem)")
    parser.add_argument(
        "--token",
        default=os.environ.get("E2E_CASHU_TOKEN"),
        help="Cashu token used to topup routstrd (or set $E2E_CASHU_TOKEN)",
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help="Path to runs.db (SQLite)"
    )
    parser.add_argument(
        "--scenarios-dir",
        default=str(DEFAULT_SCENARIOS),
        help="Directory containing scenario YAML files",
    )
    parser.add_argument(
        "--compose-file",
        default=str(DEFAULT_COMPOSE),
        help="Path to docker compose file",
    )
    parser.add_argument(
        "--providers-dir",
        default=str(DEFAULT_PROVIDERS),
        help="Directory containing upstream provider profile YAML files",
    )
    parser.add_argument(
        "--upstream-profile",
        default=os.environ.get("UPSTREAM_PROFILE"),
        help=(
            "Override the scenario YAML's upstream_profile. `mock` (default) "
            "uses the in-compose mock-openai; any other value names a "
            "providers/<id>.yaml profile and points node-a/node-b at the real "
            "provider. The provider key must be set in the matching env var "
            "(e.g. OPENAI_API_KEY) — it is passed to compose, never persisted."
        ),
    )
    parser.add_argument(
        "--upstream-max-usd",
        type=float,
        default=None,
        help=(
            "Refuse to start if the scenario's estimated upstream cost exceeds "
            "this (USD). Defaults to $UPSTREAM_MAX_USD or "
            f"${DEFAULT_UPSTREAM_MAX_USD:.2f}."
        ),
    )
    parser.add_argument(
        "--target-profile",
        choices=(TARGET_PROFILE_LOCAL, TARGET_PROFILE_REMOTE),
        default=os.environ.get("TARGET_PROFILE"),
        help=(
            "Override the scenario YAML's target_profile. `local` builds and "
            "uses the in-compose node-a/node-b; `remote` skips compose bring-up "
            "and points the daemon + tests at --remote-node-urls."
        ),
    )
    parser.add_argument(
        "--remote-node-urls",
        default=os.environ.get("REMOTE_NODE_URLS"),
        help=(
            "Comma-separated routstr node URLs (only used when "
            "target-profile is `remote`). Each URL should include scheme."
        ),
    )
    parser.add_argument(
        "--remote-admin-tokens",
        default=None,
        help=(
            "Comma-separated admin tokens, positional with --remote-node-urls "
            "(index 0 → first URL, etc.). Tokens are passed through as "
            "REMOTE_NODE_ADMIN_TOKEN_<i> env vars and are NEVER persisted. "
            "Prefer setting REMOTE_NODE_ADMIN_TOKEN_<i> env vars directly when "
            "running in CI."
        ),
    )
    args = parser.parse_args(argv)

    remote_urls = _split_csv(args.remote_node_urls)
    remote_tokens = _split_csv(args.remote_admin_tokens)
    if not remote_tokens:
        # Fall back to env-collected REMOTE_NODE_ADMIN_TOKEN_<i> so CI can hand
        # tokens through secrets without ever putting them on the command line.
        env_tokens: list[str] = []
        idx = 0
        while True:
            value = os.environ.get(f"REMOTE_NODE_ADMIN_TOKEN_{idx}")
            if value is None:
                break
            env_tokens.append(value)
            idx += 1
        remote_tokens = env_tokens

    try:
        run_id = orchestrate(
            scenario_id=args.scenario,
            token=args.token,
            db_path=Path(args.db),
            scenarios_dir=Path(args.scenarios_dir),
            compose_file=Path(args.compose_file),
            providers_dir=Path(args.providers_dir),
            target_profile_override=args.target_profile,
            upstream_profile_override=args.upstream_profile,
            remote_node_urls=remote_urls or None,
            remote_admin_tokens=remote_tokens or None,
            upstream_max_usd=args.upstream_max_usd,
        )
    except UpstreamConfigError as exc:
        # Clear, fast failure before any stack bring-up (acceptance #4/#5).
        print(f"[orchestrate] upstream config error: {exc}", file=sys.stderr)
        return 2

    # Echo a machine-friendly summary so callers (FastAPI server in ROU-134)
    # can pick up the new run id without re-querying SQLite.
    print(json.dumps({"run_id": run_id, "db": args.db}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
