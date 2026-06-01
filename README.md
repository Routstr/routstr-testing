# routstr-testing

End-to-end test harness for the Routstr stack.

## Quickstart

### 1. Sync vendor repos

```bash
make sync
```

This clones (or fast-forwards) `routstr-core`, `routstrd`, and `routstr-cli` into `vendor/`
and writes `vendor/COMMITS.txt` with the pinned commit hashes.

### 2. Configure environment

```bash
cp .env.example .env
```

Set in `.env` (all gitignored):

- `E2E_CASHU_TOKEN` — a funded cashu token, for payment tests.
- `CASHU_MINTS` — the mint the token is from (e.g. `https://mint.chorus.community`). Both nodes must trust it.
- `OPENROUTER_API_KEY` (+ optional `OPENROUTER_REFERER`) — to point the nodes at a real upstream. The node auto-seeds an `openrouter` provider at startup when this is present.
- `NODE_A_ADMIN_PASSWORD` / `NODE_B_ADMIN_PASSWORD` — admin password per node (default `test-admin-pw`), needed for `routstr-cli` config.

### 3. Start services

```bash
make up
```

> The `webui` compose service currently fails to build (corepack/pnpm on the
> node20 base image), which aborts `make up`. Bring up only the core services
> until that's fixed, and build the UI on the host (node 22 / pnpm 10) instead:
>
> ```bash
> docker compose up -d --build relay mock-openai node-a node-b routstrd cli-runner
> # UI: make serve  (host build, single origin :8000 — see "Deploying the Web UI")
> ```

### 4. Run tests

```bash
make test
```

Or drive a single scenario through the orchestrator and persist results to `runs.db`:

```bash
python -m runner.orchestrate --scenario smoke --token <cashu-token>
# or
make orchestrate SCENARIO=smoke TOKEN=cashuA...
```

Useful env flags:

- `SKIP_SYNC=1` — skip `scripts/sync.sh`
- `KEEP_UP=1` — leave compose services running after the run for debugging

The orchestrator writes one row to `runs` and one row per test to `test_results` in `runs.db` (SQLite via SQLModel). Logs land in `logs/<run-timestamp>/`.

### 5. View logs

```bash
make logs
```

### 6. Stop services

```bash
make down
```

### Testing a deployed node (ROU-151)

The orchestrator can point the test suite at **externally-deployed routstr
nodes** instead of building `node-a` / `node-b` from `vendor/routstr-core/`:

```bash
python -m runner.orchestrate \
    --scenario smoke \
    --target-profile remote \
    --remote-node-urls https://node1.example,https://node2.example
```

In `remote` mode:

- `docker compose up` is skipped — your deployment isn't touched.
- `TARGET_PROFILE=remote`, `REMOTE_NODE_URLS=...`, and
  `ROUTSTRD_BOOTSTRAP_PROVIDERS=...` are exported into pytest's env. The
  routstrd seed-providers step picks the latter up so the daemon routes
  through the remote nodes.
- The `tests/conftest.py` skip-rule auto-skips any test tagged
  `@pytest.mark.destructive`, and skips `@pytest.mark.admin_required` tests
  unless at least one `REMOTE_NODE_ADMIN_TOKEN_<i>` env var is set.
- The resulting `runs` row carries `target_profile=remote` and
  `remote_node_urls_json`. Admin tokens are never persisted.

Pass per-node admin tokens via env (preferred) or `--remote-admin-tokens`
(local dev only — argv is visible in `ps`):

```bash
REMOTE_NODE_ADMIN_TOKEN_0=secret1 REMOTE_NODE_ADMIN_TOKEN_1=secret2 \
python -m runner.orchestrate --scenario smoke \
    --target-profile remote \
    --remote-node-urls https://node1.example,https://node2.example
```

The Web UI Run modal exposes the same fields: a `target_profile` dropdown,
a node-URLs textarea, and a masked admin-token field per node. The Runs
table shows the profile badge per row and a filter in the header.

### Testing against a real upstream (ROU-153)

By default the routstr nodes talk to the in-compose `mock-openai` container
(`upstream_profile=mock`). You can instead point them at a **real upstream LLM
provider** — OpenAI, Anthropic, OpenRouter, Groq, Together, Fireworks — by
selecting a profile from [`providers/`](providers/README.md):

```bash
UPSTREAM_PROFILE=openai OPENAI_API_KEY=sk-... \
python -m runner.orchestrate --scenario openai_chat_completions
```

What happens:

1. The orchestrator loads `providers/openai.yaml`, **validates** that every
   `required_env` (here `OPENAI_API_KEY`) is set — bailing with a clear error
   before any stack bring-up if not.
2. It checks the scenario's `estimated_upstream_cost_usd` against
   `UPSTREAM_MAX_USD` (default `$1.00`) and refuses to start if over budget.
3. It exports `UPSTREAM_BASE_URL`, `UPSTREAM_API_KEY`, and `UPSTREAM_MODELS_PATH`
   so `node-a`/`node-b` route to the real provider, then runs the scenario's
   `real_upstream`-tagged tests (default-skipped under `mock`).
4. The run row records `upstream_profile=openai`, `upstream_estimated_cost_usd`,
   and a best-effort `upstream_actual_cost_usd` priced from the provider's
   model catalog. **Provider API keys are never persisted.**

Cost controls:

```bash
# Block a scenario whose estimated cost exceeds the ceiling:
UPSTREAM_PROFILE=openai OPENAI_API_KEY=sk-... UPSTREAM_MAX_USD=0.001 \
python -m runner.orchestrate --scenario openai_chat_completions
# → exits non-zero: "estimated upstream cost $0.0100 exceeds UPSTREAM_MAX_USD $0.0010"
```

The profile/target matrix:

| target  | upstream      | runs                                              |
|---------|---------------|---------------------------------------------------|
| local   | mock          | everything (current default)                      |
| local   | real provider | everything; `real_upstream` tests charge          |
| remote  | mock          | ROU-151 read-only flow (invalid for `real_upstream` tests) |
| remote  | real provider | `safe_for_remote` ∪ `real_upstream`, cost-gated   |

In the **Web UI Run modal**, an "Upstream provider" dropdown (populated from
`GET /api/providers`) exposes masked, write-only key fields for the selected
provider and a cost preview (red when over `UPSTREAM_MAX_USD`). The Runs table
gains an "Upstream" column; the Run detail shows the resolved profile and the
estimated / actual USD spend. See [`providers/README.md`](providers/README.md)
to add a provider.

## Routing + payment scenarios

Beyond the smoke / real-upstream scenarios, the harness ships integration
scenarios under `tests/integration/` (driven via the orchestrator, results in
`runs.db` + the Runs UI). The stack must be up (`make up`) and `KEEP_UP=1` is
recommended so the orchestrator runs pytest against the already-running stack.

### Cheapest-provider routing (`routstrd_cheapest`)

routstrd discovers nodes from the Nostr relay and routes each request to the
**cheapest** node that serves the model (lowest `provider_fee`, exposed at
`GET /models/<id>/providers`, sorted by `sats_pricing.max_cost`). The scenario
sets per-node fees with `routstr-cli` and asserts the ranking follows:

```bash
SKIP_SYNC=1 KEEP_UP=1 python -m runner.orchestrate --scenario routstrd_cheapest --token placeholder
```

Fee update via CLI (what the test does):

```bash
docker exec routstr-testing-cli-runner-1 bun /app/dist/index.js \
  --node http://node-a:8000 providers update 1 -t <admin-token> --fee 0.3
```

> **Relay isolation:** the bundled `@routstr/sdk` hardcodes public discovery
> relays with no env override, so an unpatched daemon discovers the *global*
> routstr network. `vendor-dockerfiles/routstrd.Dockerfile` rewrites those to
> the local `ws://relay:8080` so only `node-a`/`node-b` are discovered.

### Real paid inference (`real_inference`)

Real `/v1/chat/completions` across many models through a node, paid from a
funded ecash balance (ecash → node → openrouter → completion). Provide a funded
node api-key (or cashu token) via `NODE_A_API_KEY`:

```bash
NODE_A_API_KEY=sk-... SKIP_SYNC=1 KEEP_UP=1 \
python -m runner.orchestrate --scenario real_inference --token placeholder
```

### X-Cashu pay-per-request (`xcashu`)

Single-use ecash: send `X-Cashu: <token>` (no auth); the node redeems, charges
exact cost, and returns change in the `X-Cashu` response header. Provide one
funded token per model (plus one for the change test) via `X_CASHU_TOKENS`
(comma-separated):

```bash
X_CASHU_TOKENS=cashuB...,cashuB...,... SKIP_SYNC=1 KEEP_UP=1 \
python -m runner.orchestrate --scenario xcashu --token placeholder
```

### Spend telemetry

Node billing is sub-sat (millisats), so the Runs table renders precise spend:
paid tests append their spend to `$SPEND_REPORT_PATH` (set automatically by the
orchestrator); the run records `token_consumed_msats` and the UI shows e.g.
`349 msat` or `4 sats` instead of a rounded `0 sats`. See
[`docs/PLAN-full-node-coverage.md`](docs/PLAN-full-node-coverage.md) for the
full status, findings, and known vendor bugs (incl. a node refund/X-Cashu
change-retention fund leak — avoid repeated real-money runs until fixed).

## Deploying the Web UI for testing

The Run modal (cashu token + provider keys, target/upstream profile) and the
Scenarios/Runs views can be driven entirely from a browser — no env-var token
required. To stand up a browsable instance on one origin:

```bash
make serve              # builds webui/dist, serves UI + /api on 0.0.0.0:8000
# open http://localhost:8000  → Scenarios → "Run scenario" → paste cashu token
```

`make serve` runs `webui-build` then launches the FastAPI server with
`WEBUI_DIST_DIR` pointed at the build output, so the **same process serves both
the React UI (`/`, with SPA deep-link fallback) and the `/api/*` backend**. One
port means one tunnel / one reverse-proxy host exposes the whole harness:

```bash
# Public URL for a quick shared test (any tunnel works):
ngrok http 8000         # → https://<id>.ngrok-free.app  (UI + API, same origin)
# or put Caddy/nginx in front of :8000 on a host you control.
```

Because UI and API share an origin, the browser uses same-origin `fetch` and
`VITE_API_BASE_URL` can stay empty. The cashu token and provider API keys are
write-only — posted in the run body, forwarded to the orchestrator via env,
and never persisted or echoed (`tests/test_server_token_hygiene.py`).

> Run **execution** still needs the local docker compose stack (`make up`) for
> `target_profile=local`, or reachable `remote` node URLs entered in the modal.
> A persistent hosted deployment (containerized server with docker access) is
> tracked separately — see the ROU-125 follow-up.

## Services

| Service      | Description                                      |
|-------------|--------------------------------------------------|
| `relay`      | Nostr relay (nostr-rs-relay)                     |
| `mock-openai`| WireMock-based OpenAI API mock                   |
| `node-a`     | routstr-core node A                              |
| `node-b`     | routstr-core node B                              |
| `routstrd`   | routstrd daemon; discovery relay-isolated to local `relay` (override Dockerfile adds sqlite + `cocod` wallet) |
| `cli-runner` | routstr-cli test runner container                |
| `webui`      | Vite + React UI (Docker build currently broken — build on host) |

## Directory layout

```
vendor/           # auto-populated by make sync (gitignored)
  routstr-core/
  routstrd/
  routstr-cli/
  COMMITS.txt     # pinned commit SHAs
scripts/
  sync.sh         # vendor sync script
runner/           # scenario-driven orchestrator
  orchestrate.py  # CLI entrypoint
  models.py       # SQLModel schema (scenarios, runs, test_results)
  scenario.py     # YAML loader
  providers.py    # upstream provider profile registry (ROU-153)
  cost.py         # best-effort upstream cost pricing
  junit.py        # junit XML parser
  compose.py      # docker compose wrappers
providers/        # upstream provider profiles + curated model catalogs
  <id>.yaml       # one per provider (openai, anthropic, ...)
  models/<id>.json
scenarios/        # YAML scenario library (smoke, routstrd_cheapest, real_inference, xcashu, ...)
tests/            # pytest suite driven by the orchestrator
  cli/            # routstr-cli tests (via docker exec)
  integration/    # routing + paid scenarios (cheapest, real_inference, xcashu) + spend helper
vendor-dockerfiles/ # local Dockerfile overrides (routstr-cli, routstrd: sqlite + cocod + relay isolation)
docs/             # status / plan / findings (PLAN-full-node-coverage.md)
webui/            # Vite + React UI (build on host: pnpm install && pnpm build)
compose.yml
Makefile
pyproject.toml    # runner dependencies (sqlmodel, pyyaml, pytest, ...)
.env.example
```
