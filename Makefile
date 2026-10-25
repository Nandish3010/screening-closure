.PHONY: claude-smoke claude-record redteam-generate triage-serve up-obs check-full evaluate-smoke test-redteam validate validate-check validate-targets evaluate-refusal evaluate-abudhabi oidc-up oidc-down test-oidc up-scale agency-run test-demo test-e2e-live demo-scenario demo-stage demo-snapshot demo-restore demo-reset demo-kill-scheduler check docs-check docs-numbers tracker-lint test-security test-tools test-load load-check up down logs test test-db test-db-create test-db-reset test-chaos synthea demo-seed demo-seed-clean evaluate evaluate-capacity evaluate-capacity-check simulate evaluate-check sensitivity sensitivity-check reset web-install web-dev web-build web-test web-e2e test-e2e review-packs review-packs-check test-review-packs test-phi-logs rbac-matrix test-offline fresh-clone risk-report risk-check calibrate-fetch calibrate-fit calibrate-check effort effort-check video video-check video-deps verify hl7-demo hl7-samples judge-headline judge-headline-check

up:
	docker compose up -d --build

# A31 (ADR-041): two API replicas behind the Caddy proxy (host port PROXY_PORT, default 8100) and two schedulers; the schedulers
# talk to the API through the proxy, and every scheduler call must carry a current leader token.
up-scale:
	SCHEDULER_API_URL=http://proxy:8080 REQUIRE_LEADER_FENCE=true docker compose --profile scale up -d --build

# F14: the stack plus the observability profile (OpenTelemetry Collector, Prometheus on 127.0.0.1:9090, Grafana on 127.0.0.1:3000,
# login admin / GRAFANA_ADMIN_PASSWORD, default closure_grafana_dev). The API exports OTLP to the collector and logs JSON.
# docs/governance/deployment.md.
up-obs:
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 LOG_FORMAT=json docker compose --profile obs up -d --build

down:
	docker compose --profile scale --profile obs down

logs:
	docker compose logs -f --tail=100

# A29 (ADR-040): the local OIDC provider (Keycloak, a UAE PASS stand-in) and the API in AUTH_MODE=oidc. Generates per-run
# demo passwords unless they are in .env (KC_PW_*), prints where they are (.oidc-demo-credentials, mode 600, git-ignored).
# The web app then needs VITE_AUTH_MODE=oidc (docs/governance/identity.md). `make oidc-down` returns the stack to dev mode.
oidc-up:
	sh tools/identity/oidc_up.sh up

oidc-down:
	sh tools/identity/oidc_up.sh down

# The committed realm against a real Keycloak (own compose project, port 18180, throw-away passwords); skips without Docker.
test-oidc:
	PYTHONPATH=$(CURDIR)/api $(PY) -m pytest api/tests/test_oidc_keycloak.py -q -m oidc -rs

# F15: pytest-xdist, one worker per core, whole files per worker (XDIST= to run serially). The integration tests give each worker its own
# database, closure_test_gw<N>, copied from the migrated template closure_test_tpl (api/tests/xdist_db.py).
XDIST ?= -n auto --dist loadfile

# Unit tests only (no database needed). Uses the local .venv if present, otherwise
# runs the api tests in the `test` stage of the api image (service api-test, profile "tests"): the api image that
# runs in compose has no pytest and no tests (SEC-1). The simulator tests need the venv.
test:
	@if [ -x .venv/bin/pytest ]; then \
		.venv/bin/pytest api/tests simulator/tests -q -m "not integration and not chaos and not oidc" $(XDIST); \
	else \
		docker compose --profile tests run --rm --no-deps api-test pytest tests -q -m "not integration and not chaos and not oidc"; \
	fi

# A27 (ADR-038): exhaustive model checking of the closure engine's state machine. Drives the real `step()` breadth-first
# over a finite abstraction (event types x actors x representative payloads) until the abstract state space saturates, in
# three feature slices, and checks I1-I7 on every reachable state and transition; prints a shortest counterexample and
# exits non-zero on a violation. Pure Python, no database, under a minute. docs/governance/verification.md.
verify:
	PYTHONPATH=$(CURDIR)/api $(PY) tools/verify/model_check.py

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
	$(PSQL_ADMIN) -c "DROP DATABASE IF EXISTS closure_test_tpl WITH (FORCE)"
	$(PSQL_ADMIN) -c "CREATE DATABASE closure_test OWNER closure"

# CI (SKIP_DB_CREATE=1) creates closure_test itself, against a Postgres service container with no compose stack.
test-db: $(if $(SKIP_DB_CREATE),,test-db-create)
	DATABASE_URL=$(TEST_DATABASE_URL) $(if $(wildcard .venv/bin/pytest),.venv/bin/pytest,python3 -m pytest) api/tests -q -m "integration and not chaos" $(XDIST)

# Chaos tests (F3, F4, F5; T-A32, T-F3 to T-F7), all marked `chaos`:
#   * fault injection (api/tests/faults.py, honoured only when APP_ENV=test): duplicate delivery, clock skew,
#     a store failure after the event insert;
#   * T-A31 (api/tests/test_chaos_leader.py): kills the scheduler that holds the leader lease on a `make up-scale` stack and asserts
#     the other takes over within the lease TTL and no event is lost or duplicated; skips itself when the scale stack is not
#     up. Run it only against your own compose project (COMPOSE_PROJECT_NAME, shifted ports; the test header has the recipe);
#   * the sweep killed part-way through 5,000 cases (SIGKILL, single process and sharded), rerun, exactly the
#     planned events once;
#   * garbage from the drafting model, and consent withdrawn between the approval and the send;
#   * T-A32 (api/tests/test_chaos.py): stops and starts the scheduler container of the running stack and watches the
#     watchdog raise and clear its alert. It skips itself when the stack is not up; the others need only Postgres on
#     the compose stack (each creates and drops its own scratch database, never the dev one).
test-chaos:
	PYTHONPATH=$(CURDIR)/api DATABASE_URL=$(TEST_DATABASE_URL) .venv/bin/pytest api/tests -q -s -m chaos

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

# Finite capacity (ticket B19, milestone M6): the same replications and both scenarios, with a shared colonoscopy booking calendar and a daily
# staff-minutes budget for escalations (simulator/params.capacity.yaml layered on the scenario stacks); a base-capacity headline, a capacity
# sensitivity (70/85/100/115% of the slots), a staffing sensitivity (50 to 300%) and a slots-only / staff-only decomposition. Writes
# docs/results/capacity.json and splices the "Under finite capacity" section into docs/results/results.md in place.
# It is a SEPARATE target because it is about 30 simulations per replication: about 3 minutes with 8 workers (REPS=20), against about
# 10 seconds for `make evaluate`, which would be pushed past a minute, and past 10 minutes for `evaluate-check`, which reruns single-process.
# `make evaluate` copies the section from the committed capacity.json, so results.md is complete after either target. Run
# `make evaluate-capacity` after any change to the engine, the parameters or the seed, then `make evaluate` (or just keep results.md as
# `make evaluate-capacity` leaves it).
CAP_PARAMS ?= simulator/params.capacity.yaml
CAP_JOBS ?= 8
evaluate-capacity:
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate-capacity --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --capacity-params $(CAP_PARAMS) --jobs $(CAP_JOBS) --out-dir docs/results

# Runs evaluate-capacity twice into temp dirs on a reduced size (CAP_CHECK_REPS replications, the second time single-process) and requires identical capacity.json.
CAP_CHECK_REPS ?= 2
evaluate-capacity-check:
	@set -e; a=$$(mktemp -d); b=$$(mktemp -d); \
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate-capacity --reps $(CAP_CHECK_REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --capacity-params $(CAP_PARAMS) --jobs $(CAP_JOBS) --out-dir $$a >/dev/null; \
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate-capacity --reps $(CAP_CHECK_REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --capacity-params $(CAP_PARAMS) --jobs 1 --out-dir $$b >/dev/null; \
	if diff -q $$a/capacity.json $$b/capacity.json >/dev/null; then \
		echo "evaluate-capacity-check: PASS, capacity.json identical across runs and process counts (sha256 $$(shasum -a 256 $$a/capacity.json | cut -c1-16))"; rm -rf $$a $$b; \
	else echo "evaluate-capacity-check: FAIL, capacity.json differs ($$a vs $$b)"; diff $$a/capacity.json $$b/capacity.json | head -20; exit 1; fi

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

# The orchestrated arm's informed-refusal path switched on (A33, ADR-042; simulator/params.refusal.yaml). The full run goes to a scratch
# directory, not to docs/results: it moves the labour results the submission quotes, so it is NOT the headline (adopting it is the results'
# owner's decision). Its summary goes to docs/results/refusal.json and the secondary-scenario section of results.md (simulator/sim/refusal_report.py);
# `make evaluate` copies that section from the committed refusal.json. REFUSAL_OUT=<dir> to choose where the full run goes.
REFUSAL_OUT ?= /tmp/refusal-path
evaluate-refusal:
	mkdir -p $(REFUSAL_OUT)
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --params simulator/params.refusal.yaml --out-dir $(REFUSAL_OUT)
	$(SIMENV) $(PY) -m simulator.sim.refusal_report $(REFUSAL_OUT)/results.json docs/results

# Abu Dhabi-shaped baseline sensitivity (E20b; simulator/params.abudhabi.yaml): baseline calibrated to the 30.5% Abu Dhabi audit, reach and every
# orchestration parameter unchanged. A SENSITIVITY run, not the headline. The full run goes to a scratch directory; its summary goes to
# docs/results/abudhabi.json and a section of results.md (simulator/sim/abudhabi_report.py); `make evaluate` copies that section from the committed
# abudhabi.json. ABUDHABI_OUT=<dir> to choose where the full run goes.
ABUDHABI_OUT ?= /tmp/abudhabi-path
evaluate-abudhabi:
	mkdir -p $(ABUDHABI_OUT)
	$(SIMENV) $(PY) -m simulator.sim.cli evaluate --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) $(PARAM_FLAGS) --params simulator/params.abudhabi.yaml --out-dir $(ABUDHABI_OUT)
	$(SIMENV) $(PY) -m simulator.sim.abudhabi_report $(ABUDHABI_OUT)/results.json docs/results

# Human effort per case (ticket B17): the Human effort section of docs/results/results.md is written by `make evaluate` (it is computed from the same
# event logs, no extra run). `make effort` prints it from the committed results.json; `make effort-check` fails if results.json predates the effort
# model in simulator/sim/effort.py or docs/evidence.md section 11 lacks a source, and runs the effort tests (hand-computed arithmetic on the five-case scenario).
# E19: the /judge page figures (web/src/judge/headline.json) are generated from docs/results/results.json, not retyped.
judge-headline:
	$(PY) tools/judge/make_headline.py

judge-headline-check:
	$(PY) tools/judge/make_headline.py --check

effort:
	$(SIMENV) $(PY) -m simulator.sim.cli effort

effort-check:
	$(SIMENV) $(PY) -m simulator.sim.cli effort --check
	$(SIMENV) $(PY) -m pytest simulator/tests/test_effort.py -q

# One-at-a-time parameter sensitivity (ticket B14): every parameter with a `range` in simulator/params.yaml (and the overlay) is
# moved to its low and to its high end with the rest held at the literature-anchored scenario; 5 replications x 1,000 cases x 360 days
# per point, about 1.5 minutes with 8 workers. Writes docs/results/sensitivity.{json,md,svg}. The three files are byte-for-byte
# reproducible; `make sensitivity-check` reruns the sweep and fails unless the committed files are identical to it.
SENS_REPS ?= 5
SENS_CASES ?= 1000
SENS_JOBS ?= 8
sensitivity:
	$(SIMENV) $(PY) -W ignore -m simulator.sim.cli sensitivity --reps $(SENS_REPS) --cases $(SENS_CASES) --days $(EVAL_DAYS) --seed $(SEED) --jobs $(SENS_JOBS) --out-dir docs/results

sensitivity-check:
	$(SIMENV) $(PY) -W ignore -m simulator.sim.cli sensitivity --reps $(SENS_REPS) --cases $(SENS_CASES) --days $(EVAL_DAYS) --seed $(SEED) --jobs $(SENS_JOBS) --out-dir docs/results --check

reset:
	docker compose down -v
	docker compose up -d --build

# Generates the synthetic cohort inside a container (never on the host). Output: data/synthea/out/fhir/
synthea:
	@mkdir -p data  # created here, not by docker's bind mount: on Linux docker would make ./data root-owned and the host scripts could not write data/results
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

# E9: stage the demo's SECOND case through the API: the second positive FIT in seed order, walked to
# confirmatory_completed and owned by the specialist login (Dr. Karim Nasser), simulation clock untouched. Needs the
# stack up and seeded. Safe to rerun: if the case is already there (or past it) it says so and does nothing.
# `python3 tools/demo/stage_second_case.py --show` prints which case it is.
demo-stage:
	$(PY) tools/demo/stage_second_case.py

# Save the current database (cases, events, drafts, notifications, the sim clock AND HAPI's FHIR data) as the demo
# snapshot, on a clean seeded stack: `make reset && make demo-seed && make demo-snapshot`. It stages the second case
# first (demo-stage), so `make demo-reset` brings that case back with everything else.
demo-snapshot: demo-stage
	tools/demo/snapshot.sh save

demo-restore:
	tools/demo/snapshot.sh restore

# D15: the fine-tuned reply-triage model on the host (MLX, tools/triage/.venv), for the compose API to call at
# TRIAGE_REMOTE_URL (127.0.0.1:8765). Start it BEFORE the demo (it warms up in a few seconds); without it replies fail safe to a person.
triage-serve:
	tools/triage/.venv/bin/python tools/triage/serve.py

# One click: restore the snapshot, reset the sim clock, check the state hash equals the snapshot's (about 25 s). Start `make triage-serve`
# (own terminal) before the demo so the reply-triage cameo reads replies live.
demo-reset:
	@curl -sf -m 2 http://127.0.0.1:8765/health >/dev/null || echo 'note: the triage server is not running (make triage-serve); replies will fail safe to a person'
	tools/demo/snapshot.sh reset

# T-E9 and T-E10 against the running stack: the scenario end to end and identical after a reset, and the reset
# returns exactly the snapshot's state in under 30 s. Drives docker and leaves the demo state reset (about 4 minutes).
test-demo:
	PYTHONPATH=api $(PY) -m pytest tools/demo/tests -q -m live -s

# F11 rehearsal: stop the scheduler, wait for the dashboard alert, restart it, check no event was lost.
demo-kill-scheduler:
	$(PY) tools/demo/kill_scheduler.py

# A28 (ADR-039): send a positive FIT result as an HL7 v2 ORU^R01 to POST /hl7 (tools/hl7/samples/oru_fit_positive.hl7, a patient
# of the seeded cohort) and show the case appear. Needs the stack up and seeded. Run twice: the second ACK says "duplicate" and no
# second case appears (idempotent on MSH-10). `make hl7-demo FRESH=1` gives the message a new MSH-10, which creates a new case
# for the same patient: `make demo-reset` afterwards on the demo snapshot. The optional MLLP listener: `docker compose --profile
# hl7 up -d hl7-listener`, then `python3 tools/hl7/send.py --mllp localhost:2575 tools/hl7/samples/*.hl7`.
hl7-demo:
	PYTHONPATH=api $(PY) tools/hl7/demo.py $(if $(FRESH),--fresh)

# Rewrite tools/hl7/samples/ from the seeded cohort (synthetic messages, one per supported type).
hl7-samples:
	$(PY) tools/hl7/make_samples.py

# A22: one agency run on demand against the running stack (POST /agency/run as the supervisor, or with AGENCY_SERVICE_TOKEN
# set), printing the run summary. Compose sets AGENCY_EVERY_SWEEPS=0 so the scheduler never starts one by itself and the
# demo snapshot stays deterministic; this is how the agency is shown. Then open /agency in the web app.
agency-run:
	$(PY) tools/demo/agency_run.py

# ---- Cut F: hardening, reproducibility, `make check` (F1, F6, F9) ----------------------------------
# `make check` (F15) is the FAST tier: test, verify, test-db, web-test, web-build, web-e2e, docs-check, tracker-lint, test-security,
# test-tools, test-redteam, load-check, review-packs-check, test-review-packs, effort-check and evaluate-smoke, run concurrently
# (CHECK_JOBS, default min(cores, 6); CHECK_SERIAL=1 for one at a time, in order, stopping at the first failure). Each stage is its
# own target. CHECK_KEEP_GOING=1 runs every stage even after a failure. The simulator-heavy stages join automatically when the diff
# against main touches the simulator, the pathways or their parameters; `make check-full` always runs them.
check:
	$(PY) tools/check/run_check.py --tier fast $(if $(CHECK_KEEP_GOING),--keep-going) $(if $(CHECK_SERIAL),--serial)

# Everything: the fast tier plus the simulator-heavy checks (sensitivity-check, full evaluate-check, risk-check, calibrate-check,
# validate-check, evaluate-capacity-check). `make check` adds those by itself when the diff against main touches simulator/,
# config/pathways/, simulator params or the other paths in tools/check/run_check.py (HEAVY_PATH_PREFIXES); CHECK_HEAVY=1/0 forces.
check-full:
	$(PY) tools/check/run_check.py --tier full $(if $(CHECK_KEEP_GOING),--keep-going) $(if $(CHECK_SERIAL),--serial)

# The evaluate determinism check at 1 replication (the fast tier's smoke; `evaluate-check` is the full 20).
evaluate-smoke:
	$(MAKE) --no-print-directory evaluate-check REPS=1

# Broken relative links and ragged tables in docs/ and project/ (README.md too), and any TODO in docs/
# outside lines carrying an H1/H2 review marker.
docs-check:
	$(PY) tools/check/docs_check.py --readme
	$(PY) tools/check/docs_numbers.py

# B10 (E20b): every decimal %, pp figure and ratio quoted in README.md and docs/submission/*.md exists in docs/results or the
# sources it may come from; remaining exceptions are listed with reasons in tools/check/docs_numbers_allow.yaml. Runs as part of `make docs-check` (a `make check` stage).
docs-numbers:
	$(PY) tools/check/docs_numbers.py

# Every T-ID in TRACKER.md is in TEST-CASES.md and vice versa; done rows cite commits that exist;
# milestone counts match the rows; LOG.md is chronological.
tracker-lint:
	$(PY) tools/check/tracker_lint.py

# Security gates, none of which needs the stack: secrets and repo hygiene (F9: token shapes, private keys, JWT-looking
# strings, password literals, .env ignored and never in history, .env.example placeholders only); the RBAC matrix
# (F7, T-F9: every route and event type against every role, own case and other case, probed on the app and compared
# with the policy tables; a route without a policy decision fails; docs/governance/rbac-matrix.md must equal a fresh
# render, `make rbac-matrix` rewrites it); no PHI in URLs (F8, T-F11: path parameters are UUIDs, every query parameter
# reviewed); and the demo walk with marker values in-process, every log record scanned for the markers, e-mails and
# phone-like digit runs (T-F10). The same scan against the running stack's container logs is `make test-phi-logs`.
test-security:
	$(PY) tools/check/secrets_scan.py
	$(PY) tools/check/compose_lint.py
	PYTHONPATH=api $(PY) tools/check/rbac_matrix.py --check
	PYTHONPATH=api $(PY) tools/check/phi_scan.py routes
	PYTHONPATH=api $(PY) tools/check/phi_scan.py walk
	PYTHONPATH=api $(PY) -m pytest tests/test_rbac_matrix.py tests/test_phi_scan.py tests/test_compose_lint.py -q

rbac-matrix:
	PYTHONPATH=api $(PY) tools/check/rbac_matrix.py --write

# T-F10, T-F11 against the RUNNING stack (`make up` first; API_URL or API_PORT if not on :8000): walks the flow with
# marker values (display names, patient_ref, free text that the payload schemas must refuse), advances the sim clock
# so the scheduler and watchdog act, then scans `docker compose logs` of api (which holds the uvicorn access log),
# scheduler and watchdog; fhir and postgres are scanned and reported but not gated. It writes a few synthetic marker
# cases (deterministic ids) into that stack's database and resets the sim clock to its start: run it on a throwaway
# stack, or `make reset` afterwards. Not part of `make check`.
test-phi-logs:
	PYTHONPATH=api $(PY) tools/check/phi_scan.py live

# T-F14: the demo flow with the api, HAPI and Postgres on a compose network that has no route out (docker-compose.offline.yml),
# LLM_NETWORK_ENABLED=false, in its own compose project and volumes; also proves the block from inside the containers.
test-offline:
	PYTHONPATH=api $(PY) tools/check/offline_check.py

# T-F13 (F12): clones this repository into a temp dir and runs the fresh-clone steps there against its own compose
# project and shifted ports, timing each; writes docs/results/fresh-clone.md-style checklist output for the second machine (H5).
fresh-clone:
	sh tools/check/fresh_clone.sh

# Cross-cutting tests in tests/ (scanner, linters, load harness). PYTHONPATH=api makes a git worktree
# test its own api/ rather than the checkout the venv was installed from.
# tests/redteam is excluded: its conftest.py would shadow tests/conftest.py (both import as "conftest"); it runs under test-redteam.
test-tools:
	PYTHONPATH=api $(PY) -m pytest tests --ignore=tests/redteam tools/demo/tests tools/video/tests -q -m "not live"

# D14: the adversarial red-team suite (tests/redteam/, docs/governance/red-team.md). No database, no network. It rewrites
# tests/redteam/results.json and results.md (deterministic; commit them when the suite changes).
test-redteam:
	PYTHONPATH=api $(PY) -m pytest tests/redteam -q

# A35 (ADR-043): MANUAL targets that call the Claude API (need ANTHROPIC_API_KEY in the environment or the gitignored .env; never in CI).
# claude-smoke: one tiny call, prints success or failure only. claude-record: re-record api/app/ai/cache/ (drafts + one agency run).
# redteam-generate: ask Claude for adversarial variants into tests/redteam/generated/ (committed; `make test-redteam` replays them).
claude-smoke:
	$(PY) tools/claude/smoke.py

claude-record:
	$(PY) tools/claude/record_demo.py

redteam-generate:
	$(PY) tools/claude/redteam_generate.py

# Scheduler sweep over 1k / 10k / 100k seeded open cases in a throwaway `closure_load` database on the
# compose Postgres (:5433); rewrites docs/results/load.md. KEEP_LOAD_DB=1 keeps the database.
test-load:
	PYTHONPATH=api $(PY) tools/load/time_sweep.py

# The 1k size only, with the same correctness assertions and no files written (part of `make check`).
load-check:
	PYTHONPATH=api $(PY) tools/load/time_sweep.py --check

# ---- risk score (D8, D10) ------------------------------------------------------------------
# Retrains the loss-to-follow-up risk score from the seed on the simulator data and rewrites api/app/risk/model.json,
# docs/results/risk/ (risk.json, training.md, calibration.svg) and the generated blocks of docs/governance/risk-score-card.md.
# Pure Python, no database, about a minute. Two runs on the same inputs write identical bytes (T-D11).
risk-report:
	$(SIMENV) $(PY) -m simulator.sim.export_training

# Fails if any of those committed files differs from a fresh retrain (T-D12); part of `make check`.
risk-check:
	$(SIMENV) $(PY) -m simulator.sim.export_training --check

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

# ---- calibration from open external datasets (ticket B18) -------------------------------------------------
# The downloaded files are UNTRUSTED input: they live only under data/external/<source>/ (gitignored), are only parsed (zip, OLE2, text), never
# executed, and every script that reads them runs as `python3 -I` (isolated mode). Manifest (URL, licence, sha256, size, download time):
# tools/calibration/manifest.json. Results: tools/calibration/fitted.json and docs/results/calibration.md.
#   calibrate-fetch  download (skips files already present) and rewrite the manifest
#   calibrate-fit    fit every dataset from the local files and write tools/calibration/fitted.json (about 1 minute)
#   calibrate-check  fixture tests, then re-run the fits and diff them against fitted.json and the `empirical` leaves of simulator/params.yaml
calibrate-fetch:
	python3 -I tools/calibration/fetch.py

calibrate-fit:
	python3 -I tools/calibration/calibrate.py fit

calibrate-check:
	PYTHONPATH=$(CURDIR)/api:$(CURDIR) $(PY) -m pytest tools/calibration/tests -q
	python3 -I tools/calibration/calibrate.py check

# ---- B20 (M6): validation of the simulator against data it was not calibrated on -> docs/results/validation.{json,md} -------------------
# `make validate`         run the baseline/orchestrated arms against the held-out targets in tools/calibration/validation_targets.json and write the two files
#                         (20 replications x 2,000 cases x 360 days, about 1 to 2 minutes with 8 workers; reads no external file, so it runs from a clean clone).
# `make validate-check`   regenerate twice (multi-process, single-process) and fail unless both agree with each other and with the committed files.
# `make validate-targets` rebuild the targets file from the local external files (python3 -I parsers, sha256 checked against the manifest; needs
#                         `make calibrate-fetch` first); with CHECK=1 it only fails if the result differs from the committed file.
VAL_JOBS ?= 8
validate:
	$(SIMENV) $(PY) -W ignore -m simulator.sim.validation run --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) --jobs $(VAL_JOBS)

validate-check:
	$(SIMENV) $(PY) -W ignore -m simulator.sim.validation check --reps $(REPS) --cases $(CASES) --days $(EVAL_DAYS) --seed $(SEED) --jobs $(VAL_JOBS)

validate-targets:
	python3 -I tools/calibration/validation_targets.py $(if $(CHECK),check,build)

# ---- E16: the 3-minute submission video and the backup demo video, produced without a human recording (tools/video/) -----
# `make video-check` (no stack, no ffmpeg) validates tools/video/storyboard.yaml (every shot has route, actions, caption, narration, duration; the
# submission targets plus the cards add up to at most 180 s). `make video` does the whole run against the live stack
# (it runs `make demo-reset` and `make demo-scenario UNTIL=owner_accepted` first, stops and restarts the scheduler for the
# catch shot, and runs `make demo-reset` again at the end): record (Playwright, needs Node 22.18+), narrate (macOS `say`,
# ffmpeg), assemble (ffmpeg). Outputs: tools/video/out/submission-3min.mp4 and tools/video/out/backup-demo.mp4 (gitignored).
# About 10 minutes. See docs/submission/video.md.
video-check:
	$(PY) tools/video/storyboard.py --check

# `make video-deps` checks ffmpeg, ffprobe and macOS `say` (and the voice) are installed, with the fix in the message.
video-deps:
	$(PY) tools/video/storyboard.py --tools

video: video-check video-deps
	node tools/video/record.ts
	$(PY) tools/video/narrate.py
	tools/video/assemble.sh
