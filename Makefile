.PHONY: test-security up down logs test test-db test-db-create test-db-reset synthea demo-seed demo-seed-clean evaluate simulate evaluate-check reset web-install web-dev web-build web-test web-e2e

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

# Unit tests only (no database needed). Uses the local .venv if present, otherwise
# runs the api tests inside the api container (the api image installs the dev extras so this works
# offline; the simulator tests need the venv).
test:
	@if [ -x .venv/bin/pytest ]; then \
		.venv/bin/pytest api/tests simulator/tests -q -m "not integration"; \
	else \
		docker compose run --rm --no-deps -e DATABASE_URL= api pytest tests -q -m "not integration"; \
	fi

# Integration tests run against their own database, `closure_test`, in the same compose postgres
# (published on host port 5433). The dev database `closure` is never used: the test session
# refuses to start if DATABASE_URL names it (api/tests/conftest.py, ADR-015). The test session
# runs the migrations itself.
TEST_DATABASE_URL ?= postgresql://closure:closure_dev_password@localhost:5433/closure_test
PSQL_ADMIN := docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U closure -d postgres -At

# Creates closure_test if it does not exist (a fresh volume already has it from
# docker/postgres-init; an older volume does not).
test-db-create:
	@$(PSQL_ADMIN) -c "SELECT 1 FROM pg_database WHERE datname='closure_test'" | grep -q 1 \
		|| $(PSQL_ADMIN) -c "CREATE DATABASE closure_test OWNER closure"

# Drops and recreates the test database (empty; the next `make test-db` migrates it again).
test-db-reset:
	$(PSQL_ADMIN) -c "DROP DATABASE IF EXISTS closure_test WITH (FORCE)"
	$(PSQL_ADMIN) -c "CREATE DATABASE closure_test OWNER closure"

test-db: test-db-create
	DATABASE_URL=$(TEST_DATABASE_URL) .venv/bin/pytest api/tests -q -m integration

# Python for the simulator scripts: the project venv if present, else python3 (needs httpx).
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# Synthetic cohort pipeline against the running stack (`make up` first):
#   synthea (only if no output yet) -> load patients into HAPI -> generate positive FIT results
#   -> create one case per positive. Safe to rerun: loading is skipped when HAPI has patients,
#   results use deterministic ids, and cases use deterministic ids (409 = already created).
# Full reset: `make reset && make demo-seed`. Remove generated files only: `make demo-seed-clean`.
demo-seed:
	@if [ -n "$$(ls data/synthea/out/fhir 2>/dev/null)" ]; then \
		echo "demo-seed: using existing Synthea output in data/synthea/out/fhir (make demo-seed-clean to regenerate)"; \
	else \
		$(MAKE) synthea; \
	fi
	$(PY) simulator/load_cohort.py --skip-if-loaded
	$(PY) simulator/generate_results.py
	$(PY) simulator/create_cases.py

demo-seed-clean:
	rm -rf data/synthea/out data/results

# ---- behaviour simulator and evaluation (pure Python; no database, no network) -----------------
# The simulator imports the API's pathway loader and state machine, so api/ goes on PYTHONPATH.
SIMENV := PYTHONPATH=api:.
SEED ?= 20261017
CASES ?= 2000
REPS ?= 20
ARM ?= orchestrated
SIM_DAYS ?= 120
EVAL_DAYS ?= 360
PARAMS ?= simulator/params.yaml
PARAM_FLAGS := $(foreach f,$(PARAMS),--params $(f))

# Regenerates docs/results/results.json and results.md (every number in the docs) from the seed.
# Change a parameter in simulator/params.yaml (or layer a file: make evaluate PARAMS="simulator/params.yaml my.yaml").
evaluate:
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --out-dir docs/results

# One arm, one run, with the closure-rule and consent invariants checked on every case log.
simulate:
	$(SIMENV) $(PY) -m simulator.sim.cli run --arm $(ARM) --cases $(CASES) --days $(SIM_DAYS) --seed $(SEED) $(PARAM_FLAGS) --verify

# Runs evaluate twice into temp dirs (the second time single-process) and requires identical results.json.
evaluate-check:
	@set -e; a=$$(mktemp -d); b=$$(mktemp -d); \
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --out-dir $$a >/dev/null; \
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --out-dir $$b --jobs 1 >/dev/null; \
	if diff -q $$a/results.json $$b/results.json >/dev/null; then \
		echo "evaluate-check: PASS, results.json identical across runs (sha256 $$(shasum -a 256 $$a/results.json | cut -c1-16))"; rm -rf $$a $$b; \
	else echo "evaluate-check: FAIL, results.json differs ($$a vs $$b)"; diff $$a/results.json $$b/results.json | head -20; exit 1; fi

reset:
	docker compose down -v
	docker compose up -d --build

# Generates the synthetic cohort inside a container (never on the host). Output: data/synthea/out/fhir/
synthea:
	docker compose --profile tools run --rm synthea

# Web app (Vite + React + TS, web/). Adapter: VITE_API_ADAPTER=mock (default) or http (VITE_API_URL).
web-install:
	cd web && npm install

web-dev:
	cd web && npm run dev

web-build:
	cd web && npm run build

web-test:
	cd web && npm test

# Playwright e2e against a mock-adapter `vite preview` (needs `npx playwright install chromium` once).
web-e2e:
	cd web && npm run test:e2e

# ---- Cut F ----------------------------------------------------------------------------------------
# Secrets and repo hygiene (F9): token shapes, private keys, JWT-looking strings, password literals,
# .env ignored and never in history, .env.example placeholders only. F7 and F8 add RBAC and PHI scans here.
test-security:
	$(PY) tools/check/secrets_scan.py
