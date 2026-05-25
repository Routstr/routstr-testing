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
