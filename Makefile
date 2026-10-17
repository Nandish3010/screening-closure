.PHONY: up down logs test test-db demo-seed evaluate reset

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

# Unit tests only (no database needed). Uses the local .venv if present, otherwise
# runs inside the api container (the api image installs the dev extras so this works offline).
test:
	@if [ -x .venv/bin/pytest ]; then \
		.venv/bin/pytest api/tests -q -m "not integration"; \
	else \
		docker compose run --rm --no-deps -e DATABASE_URL= api pytest tests -q -m "not integration"; \
	fi

# Integration tests against the running compose postgres (published on host port 5433).
test-db:
	DATABASE_URL=postgresql://closure:closure_dev_password@localhost:5433/closure .venv/bin/pytest api/tests -q -m integration

demo-seed:
	@echo "TODO: create 200 cases from Synthea and walk one case to closure"

evaluate:
	@echo "TODO: regenerates every number in docs from a fixed seed"

reset:
	docker compose down -v
	docker compose up -d --build
