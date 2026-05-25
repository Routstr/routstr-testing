.PHONY: sync up down test logs orchestrate smoke

sync:
	@bash scripts/sync.sh

up:
	docker compose up -d

down:
	docker compose down

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
