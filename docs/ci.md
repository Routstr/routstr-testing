# CI lanes

This repo ships a two-lane GitHub Actions workflow (`.github/workflows/ci.yml`).

## `pr` lane — fast, no Docker, no funds (gates every PR)

Runs on every pull request and push to `main`. Steps:

1. `uv sync --group dev --frozen` — hermetic env incl. pinned `ruff`/`mypy`.
2. `ruff check .` — lint gate (config in `pyproject.toml`).
3. `mypy` — type-checks the **money-path orchestrator package** `runner/`
   only (`[tool.mypy] files = ["runner"]`). `server/` uses idiomatic SQLModel
   query expressions that trip SQLAlchemy's typing overloads and is out of
   scope for this lane.
4. `pytest tests --deselect tests/test_smoke_fail.py` with `SCENARIO_ID=smoke`.
   The full unit/mock suite. Service-backed integration tests guard themselves
   and **skip** without their env (no `REMOTE_NODE_URLS`, `X_CASHU_TOKENS`, or a
   funded daemon), so nothing here needs Docker or real sats. The mock
   money-path guard `tests/integration/test_spend_unit.py` (Cashu TokenV4
   amount decode + spend accounting) runs here.
5. **Money-path pipeline guards** — drive the orchestrator and assert its
   *process exit code*:
   - `smoke` MUST exit 0 (pipeline intact).
   - `smoke_fail` MUST exit non-zero (anti-silent-pass: a scenario whose tests
     fail has to fail the build). The step inverts the exit code so a wrong
     `0` fails CI.

No real sats, no mint, no Docker. Safe to run on forked-PR runners.

## `nightly` lane — Docker money-path (scheduled / manual)

Runs on a nightly `cron` (and `workflow_dispatch` with `run_nightly=true`).
Never runs on PRs. Brings up the compose topology (`make up`) — local nutshell
FakeWallet mints, so **real Cashu crypto, zero real sats** — and is wired to run
the `services_required` swap scenarios.

The swap scenarios (`swap_foreign_mint`, `swap_foreign_mint_retry`) are added by
[`Routstr/routstr-testing#1`](https://github.com/Routstr/routstr-testing/pull/1)
(foreign-mint swap + retry; covers `routstr-core#549`/`#468` fund-loss). Their
orchestrator steps are present but **commented out** in the workflow until PR #1
merges to `main`, so the lane never references files not yet on `main`. Once
PR #1 lands: uncomment the two steps. Pin `ROUTSTR_CORE_REF=refs/pull/549/head`
for the *retry* scenario until `core#549` merges (on `core` main it asserts the
no-retry 400/abort path), then drop the pin.

Vendor refs default to `main` (`scripts/sync.sh`) so CI tests shipped code.
