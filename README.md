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
# Edit .env and set E2E_CASHU_TOKEN if running payment tests
```

### 3. Start services

```bash
make up
```

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

## Services

| Service      | Description                                      |
|-------------|--------------------------------------------------|
| `relay`      | Nostr relay (nostr-rs-relay)                     |
| `mock-openai`| WireMock-based OpenAI API mock                   |
| `node-a`     | routstr-core node A                              |
| `node-b`     | routstr-core node B                              |
| `routstrd`   | routstrd daemon connected to relay + mock-openai |
| `cli-runner` | routstr-cli test runner container                |

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
  junit.py        # junit XML parser
  compose.py      # docker compose wrappers
scenarios/        # YAML scenario library (smoke.yaml, ...)
tests/            # pytest suite driven by the orchestrator
compose.yml
Makefile
pyproject.toml    # runner dependencies (sqlmodel, pyyaml, pytest, ...)
.env.example
```
