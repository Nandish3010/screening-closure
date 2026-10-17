.PHONY: up down logs test demo-seed evaluate reset

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

# Uses the local .venv if present, otherwise runs inside the api container
# (the api image installs the dev extras so this works offline).
test:
	@if [ -x .venv/bin/pytest ]; then \
		.venv/bin/pytest api/tests -q; \
	else \
		docker compose run --rm --no-deps api pytest tests -q; \
	fi

demo-seed:
	@echo "TODO: create 200 cases from Synthea and walk one case to closure"

evaluate:
	@echo "TODO: regenerates every number in docs from a fixed seed"

reset:
	docker compose down -v
	docker compose up -d --build
