.PHONY: sync up down test logs

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
