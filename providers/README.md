# Upstream provider profiles (ROU-153)

This directory is the **per-provider profile registry**. Each `*.yaml` here
describes one real upstream LLM provider the harness can point the in-compose
`node-a` / `node-b` at, instead of the default in-compose `mock-openai`.

Adding a provider is **editing one YAML + one model catalog — no code changes.**

## Profile schema

```yaml
# providers/<id>.yaml
id: openai                                # stable profile id (== filename stem)
name: OpenAI                              # human label shown in the UI dropdown
upstream_base_url: https://api.openai.com/v1   # routstr-core UPSTREAM_BASE_URL
api_key_env: OPENAI_API_KEY              # env var the harness reads the key from
models_file: providers/models/openai.json     # curated catalog (see below)
required_env:                            # env vars that must be set for a run
  - name: OPENAI_API_KEY                 #   a bare string → required, no default
  - name: OPENROUTER_REFERER             #   a mapping → optional when `default` set
    default: https://routstr-testing.local
notes: |
  Free-form quirks (endpoint shape, usage reporting, rate limits, …).
```

| field               | required | meaning                                                        |
|---------------------|----------|----------------------------------------------------------------|
| `id`                | yes      | Profile id; must equal the filename stem.                      |
| `name`              | yes      | Display name for the UI dropdown.                              |
| `upstream_base_url` | yes      | Becomes `UPSTREAM_BASE_URL` on node-a/node-b.                  |
| `api_key_env`       | yes      | The env var the harness reads → `UPSTREAM_API_KEY`.           |
| `models_file`       | no       | Repo-relative path to the curated catalog (`MODELS_PATH`).    |
| `required_env`      | no       | List of `name` (required) or `{name, default}` (optional).    |
| `notes`             | no       | Provider quirks. Surfaced in the API.                          |

`mock` is **not** a YAML here — it is the sentinel profile for the in-compose
`mock-openai` container (the default). Selecting any profile other than `mock`
turns a run into a real-cost run.

## Model catalogs

`providers/models/<id>.json` mirrors the OpenRouter-shaped format from
`routstr-core/models.example.json`. Ship **3–5 representative models** per
provider (one cheap, one mid-tier, one frontier) rather than the full list:

```json
{ "models": [ { "id": "gpt-4o-mini", "name": "OpenAI: GPT-4o-mini",
               "pricing": { "prompt": "0.00000015", "completion": "0.0000006" },
               "context_length": 128000, "architecture": { ... } } ] }
```

The `pricing.prompt` / `pricing.completion` per-token USD values are what the
orchestrator uses to price `upstream_actual_cost_usd` from a real run's
reported token usage. Keep them roughly current.

The catalog is mounted read-only into the node containers at
`/providers-models`; for a real profile the orchestrator sets
`MODELS_PATH=/providers-models/<id>.json`.

## How the harness uses a profile

1. `UPSTREAM_PROFILE=<id>` (CLI/env) or the UI dropdown selects the YAML.
2. The orchestrator validates every `required_env` (no-default) is set, and
   that the scenario's `estimated_upstream_cost_usd` ≤ `UPSTREAM_MAX_USD`.
3. It exports `UPSTREAM_BASE_URL`, `UPSTREAM_API_KEY`, `UPSTREAM_MODELS_PATH`
   (+ any extra `required_env`) and runs `docker compose up`, so node-a/node-b
   talk to the real provider.
4. After pytest, it prices any reported usage and records `upstream_profile`,
   `upstream_estimated_cost_usd`, `upstream_actual_cost_usd` on the run.

Provider API keys live only in env passed to compose / the orchestrator
subprocess — **never written to runs.db, never returned by the API.**

## Adding a new provider (one PR)

1. `providers/<id>.yaml` with the schema above.
2. `providers/models/<id>.json` with 3–5 curated models.
3. If the provider needs a dedicated request-shape in routstr-core, confirm an
   upstream class exists in `routstr/upstream/` (most OpenAI-compatible
   providers fall back to the generic class); otherwise file a routstr-core
   issue under ROU-31 first.
4. Done — `GET /api/providers` and the UI dropdown pick it up automatically.
