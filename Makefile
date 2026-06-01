.PHONY: sync up down test logs orchestrate smoke dump-logs server server-test webui-build serve

E2E_TIMEOUT ?= 60

sync:
	@bash scripts/sync.sh

up: sync
	@echo "Building and starting 6-service e2e topology..."
	docker compose up -d --build
	@echo "Waiting for relay..."
	@bash scripts/wait_for.sh relay    http://localhost:7777 $(E2E_TIMEOUT)
	@echo "Waiting for mock-openai..."
	@bash scripts/wait_for.sh mock-openai http://localhost:8083/v1/models $(E2E_TIMEOUT)
	@echo "Waiting for node-a..."
	@bash scripts/wait_for.sh node-a   http://localhost:8001/v1/info $(E2E_TIMEOUT)
	@echo "Waiting for node-b..."
	@bash scripts/wait_for.sh node-b   http://localhost:8002/v1/info $(E2E_TIMEOUT)
	@echo "Waiting for routstrd..."
	@bash scripts/wait_for.sh routstrd http://localhost:8091/health $(E2E_TIMEOUT)
	@echo ""
	@echo "All services healthy."
	@echo "  relay:       ws://localhost:7777"
	@echo "  mock-openai: http://localhost:8083"
	@echo "  node-a:      http://localhost:8001"
	@echo "  node-b:      http://localhost:8002"
	@echo "  routstrd:    http://localhost:8091"
	@echo ""
	@echo "Providers seen by routstrd:"
	@curl -sf http://localhost:8091/providers | python3 -c "import sys,json; d=json.load(sys.stdin); [print('  -', p['baseUrl']) for p in d.get('providers',[])]" 2>/dev/null || echo "  (none yet — discovery may still be in progress)"
	@echo ""
	@echo "CLI runner ready: docker compose exec cli-runner routstr --help"

down:
	@echo "Stopping topology and removing volumes..."
	docker compose down -v
	@echo "Done."

dump-logs:
	@bash scripts/dump_logs.sh

test:
	docker compose run --rm cli-runner bash -c "cd /tests && bash run.sh"

logs:
	docker compose logs -f

# Drive one scenario through the orchestrator. Override scenario / token at the CLI:
#   make orchestrate SCENARIO=smoke TOKEN=cashuA...
orchestrate:
	python -m runner.orchestrate --scenario $(or $(SCENARIO),smoke) --token "$(TOKEN)"

# Quick acceptance check: run the smoke scenario with sync skipped (no docker required).
smoke:
	SKIP_SYNC=1 python -m runner.orchestrate --scenario smoke --token "$(or $(TOKEN),placeholder)"

# Launch the FastAPI backend that the React UI (ROU-135) consumes.
# Override host/port at the CLI:  make server HOST=0.0.0.0 PORT=8000
server:
	uvicorn server.main:app --reload --host $(or $(HOST),127.0.0.1) --port $(or $(PORT),8000)

# Build the React UI into webui/dist so the server can serve it on one origin.
webui-build:
	cd webui && (corepack pnpm install --frozen-lockfile || pnpm install --frozen-lockfile) && \
	  (corepack pnpm build || pnpm build)

# One-command deployable: build the UI, then serve UI + /api on a single origin
# (default 0.0.0.0:8000). Point a tunnel/reverse-proxy at this one port and the
# whole harness — Scenarios, Runs, the Run modal (cashu token + provider keys)
# — is reachable from a browser. Override host/port: make serve HOST=0.0.0.0 PORT=8000
serve: webui-build
	WEBUI_DIST_DIR=$(CURDIR)/webui/dist \
	  uvicorn server.main:app --host $(or $(HOST),0.0.0.0) --port $(or $(PORT),8000)

# Run only the server test suite (fast — no docker, no real subprocess).
server-test:
	python -m pytest \
	  tests/test_server_scenarios.py \
	  tests/test_server_runs.py \
	  tests/test_server_token_hygiene.py \
	  tests/test_server_balance.py \
	  tests/test_runner_balance.py \
	  tests/test_orchestrate_balance.py \
	  -v
