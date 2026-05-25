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
tests/            # e2e test scripts
compose.yml
Makefile
.env.example
```
