.PHONY: test-demo test-e2e-live demo-scenario demo-snapshot demo-restore demo-reset demo-kill-scheduler check docs-check tracker-lint test-security test-tools test-load load-check up down logs test test-db test-db-create test-db-reset test-chaos synthea demo-seed demo-seed-clean evaluate simulate evaluate-check reset web-install web-dev web-build web-test web-e2e test-e2e review-packs review-packs-check test-review-packs

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
		.venv/bin/pytest api/tests simulator/tests -q -m "not integration and not chaos"; \
	else \
		docker compose run --rm --no-deps -e DATABASE_URL= api pytest tests -q -m "not integration and not chaos"; \
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
	DATABASE_URL=$(TEST_DATABASE_URL) .venv/bin/pytest api/tests -q -m "integration and not chaos"

# Chaos tests (T-A32): stop and start the scheduler container of the running stack and watch the
# watchdog raise and clear its alert. Needs `make up` first and docker on the host; it writes no
# cases. Until F3 adds the full chaos suite this runs only the tests marked `chaos`.
test-chaos:
	.venv/bin/pytest api/tests -q -s -m chaos

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

# Regenerates docs/results/results.json and results.md (every number in the docs) from the seed, for BOTH scenarios:
# upper_range (PARAMS, default simulator/params.yaml) and literature_anchored (the same stack plus the overlay
# simulator/params.conservative.yaml; the overlay may only touch the orchestrated arm, evaluate refuses otherwise).
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

# ---- E8: the demo scenario, one-click reset and the kill-the-scheduler rehearsal (tools/demo/, README there) ------------
# The scripted 7-minute story for one named synthetic patient, through the API: `make demo-scenario [UNTIL=<step>]`.
demo-scenario:
	$(PY) tools/demo/scenario.py $(if $(UNTIL),--until $(UNTIL))

# Save the current database (cases, events, drafts, notifications, the sim clock AND HAPI's FHIR data) as the demo
# snapshot, on a clean seeded stack: `make reset && make demo-seed && make demo-snapshot`.
demo-snapshot:
	tools/demo/snapshot.sh save

demo-restore:
	tools/demo/snapshot.sh restore

# One click: restore the snapshot, reset the sim clock, check the state hash equals the snapshot's (about 25 s).
demo-reset:
	tools/demo/snapshot.sh reset

# T-E9 and T-E10 against the running stack: the scenario end to end and identical after a reset, and the reset
# returns exactly the snapshot's state in under 30 s. Drives docker and leaves the demo state reset (about 4 minutes).
test-demo:
	PYTHONPATH=api $(PY) -m pytest tools/demo/tests -q -m live -s

# F11 rehearsal: stop the scheduler, wait for the dashboard alert, restart it, check no event was lost.
demo-kill-scheduler:
	$(PY) tools/demo/kill_scheduler.py

# ---- Cut F: hardening, reproducibility, `make check` (F1, F6, F9) ----------------------------------
# `make check` runs, in order and stopping at the first failure: test, test-db (skipped loudly if
# Postgres on :5433 is down), web-test, web-build, evaluate-check, docs-check, tracker-lint,
# test-security, test-tools, a reduced load check, review-packs-check, test-review-packs and web-e2e. Each stage is its own target. Run every stage
# even after a failure (for reporting): `make check CHECK_KEEP_GOING=1`.
check:
	$(PY) tools/check/run_check.py $(if $(CHECK_KEEP_GOING),--keep-going)

# Broken relative links and ragged tables in docs/ and project/ (README.md too), and any TODO in docs/
# outside lines carrying an H1/H2 review marker.
docs-check:
	$(PY) tools/check/docs_check.py --readme

# Every T-ID in TRACKER.md is in TEST-CASES.md and vice versa; done rows cite commits that exist;
# milestone counts match the rows; LOG.md is chronological.
tracker-lint:
	$(PY) tools/check/tracker_lint.py

# Secrets and repo hygiene (F9): token shapes, private keys, JWT-looking strings, password literals,
# .env ignored and never in history, .env.example placeholders only. F7 and F8 add RBAC and PHI scans here.
test-security:
	$(PY) tools/check/secrets_scan.py

# Cross-cutting tests in tests/ (scanner, linters, load harness). PYTHONPATH=api makes a git worktree
# test its own api/ rather than the checkout the venv was installed from.
test-tools:
	PYTHONPATH=api $(PY) -m pytest tests tools/demo/tests -q -m "not live"

# Scheduler sweep over 1k / 10k / 100k seeded open cases in a throwaway `closure_load` database on the
# compose Postgres (:5433); rewrites docs/results/load.md. KEEP_LOAD_DB=1 keeps the database.
test-load:
	PYTHONPATH=api $(PY) tools/load/time_sweep.py

# The 1k size only, with the same correctness assertions and no files written (part of `make check`).
load-check:
	PYTHONPATH=api $(PY) tools/load/time_sweep.py --check

# ---- reviewer packs (E10) ------------------------------------------------------------------
# Regenerates docs/review/clinician-pack.md and arabic-pack.md from the pathway YAML, the web
# string catalogue, the message templates and the forbidden-phrase lists (pure Python: PyYAML only).
review-packs:
	$(PY) tools/review/build_packs.py

# Fails if the committed packs differ from a fresh build.
review-packs-check:
	$(PY) tools/review/build_packs.py --check

# T-E17: packs equal a fresh build and contain every string in the sources.
test-review-packs:
	$(PY) -m pytest tools/review/tests -q

# The name the test plan uses (TEST-CASES.md): same suite as web-e2e.
test-e2e: web-e2e

# Playwright against the REAL stack (E8): an http-adapter build of the web app (own port 4175) in front of the
# running compose stack with the seeded cohort. The full flow (worklist, ownership, draft approved and sent,
# handoff, booking, clock advance with overdue and escalation, colonoscopy Procedure in HAPI, review, close with
# the signer's Practitioner, reopen), the declined-handoff re-route, and T-C16 (stops the scheduler container,
# the dashboard banner appears within 90 s, restarts it, the banner clears; about 3 minutes).
# It skips with a message when the stack is down or not seeded. It creates its own cases and advances the
# simulation clock: run `make demo-reset` afterwards to put the demo state back.
test-e2e-live:
	@if ! curl -fsS -o /dev/null http://localhost:8000/health 2>/dev/null; then \
		echo "test-e2e-live: SKIPPED, the API is not reachable at http://localhost:8000 (start the stack with 'make up' and seed it with 'make demo-seed')"; \
	else \
		cd web && npx playwright test -c playwright.live.config.ts; \
	fi
