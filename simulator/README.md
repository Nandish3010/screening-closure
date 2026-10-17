# Behaviour simulator and evaluation

Two things live here:

1. the **behaviour simulator** (`simulator/sim/`): a pure-Python discrete-event model of the positive-FIT ->
   colonoscopy -> closure pathway, run with and without our orchestration, plus the evaluation harness that
   produces every number in `docs/results/`;
2. the **synthetic cohort pipeline** (`load_cohort.py`, `generate_results.py`, `create_cases.py`, `make demo-seed`):
   Synthea patients -> HAPI -> seeded positive FIT results -> API cases. Documented at the end of this file.

The simulator is independent of the API and the database. It reuses the API's `app.pathway.load_pathway` and
`app.state_machine.apply`, so the simulator and the API share one source of truth for state transitions and for the
closure rule. All data are synthetic.

## Run it

From the repo root (no database, no network, no extra dependencies beyond `pyyaml`):

```
make test                      # unit + property tests (api and simulator)
make simulate                  # one orchestrated run: 2,000 cases x 120 days, invariants checked on every case log
make simulate ARM=baseline     # the baseline arm
make evaluate                  # 20 replications x 2 arms x 2,000 cases x 360 days -> docs/results/results.{json,md}
make evaluate-check            # evaluate twice into temp dirs (once multi-process, once single) and diff results.json

python -m simulator.sim.cli run --arm orchestrated --cases 2000 --days 120 --seed 20261017
python -m simulator.sim.cli evaluate --reps 20
```

`run` prints the event-log SHA-256, event count, runtime and the metrics; `--verify` re-checks the closure rule and
the consent invariant on every case; `--events FILE` writes the event log as JSON lines (`{arm}` in the name is replaced).
Variables for `make`: `SEED`, `CASES`, `REPS`, `ARM`, `SIM_DAYS`, `EVAL_DAYS`, `PARAMS`.

## The model

**Time** is a float number of days since `simulation.start_date` (a Monday). Staff act only on working days
(`working_week` in the parameters; Saturday-Sunday off by default; an optional holiday list). Pathway SLAs and the
escalation ladder are counted in **working days** (`simulator/sim/calendar.py`). Each case is followed for `--days`
days from the day its result arrives; results arrive over `simulation.intake_window_days`.

**Patients** (`patients.py`) have a language (ar/en/other), a preferred channel (sms/whatsapp/phone/letter), a
`contact_valid` flag (the wrong-contact rate), a provider-switch flag and a latent responsiveness factor. Each patient is
seeded from a uuid5 of (base seed, patient key), so adding patients never changes the others.

**Behaviour** (`behaviour.py`): lognormal staff and patient delays; per-attempt response probability by channel, reduced when
the message is not in the patient's language and after a patient has disengaged; no-show probability for the colonoscopy
appointment; drop-out after unanswered contacts or no-shows; consent withdrawal; a share of patients who will refuse the
colonoscopy whatever is done; referral outcomes (accepted, rejected with a reason from {capacity, wrong_service,
missing_info, patient_declined}, or never answered).

**Common random numbers.** All randomness for a patient is pre-drawn in a fixed layout (`Latents`): the k-th staff delay,
the k-th outreach response, the k-th appointment outcome, the k-th referral outcome are the same numbers in both arms. An
arm that makes more attempts (the orchestrated arm chases) simply consumes more of the same pools. Parameters turn draws into
outcomes, so changing a parameter never re-draws anything and differences between arms are attributable to the orchestration.

### The two arms (`engine.py`)

| | baseline (today) | orchestrated (our engine) |
|---|---|---|
| Owner | none (`accept_ownership` is implicit, `owner: null`) | named owner on `accept_ownership`; changes on a provider transfer |
| First outreach | one letter or SMS in a default language | patient's preferred channel and language |
| Timers | none | working-day SLA per pathway step from the YAML (`close` uses `orchestration.signoff_sla_working_days`; `complete_confirmatory` uses the DoH 31-day KPI, see below) |
| Overdue / escalation | none | `overdue` at breach, then the YAML ladder: reminder (owner), escalate (supervisor, human phone call), escalate (coordinator, alternate contact route) |
| Unreachable patient | silent loss | delivery failure detected, contact recovery attempted; ladder exhausted -> `lost_contact` flagged to a human |
| Referral | rejection or silence may never be noticed | `handoff_requested/accepted/rejected`; rejections bounce to the owner with their reason; unanswered referrals bounce on a timer |
| Appointment | no reminders; a no-show is not noticed | reminders before the appointment; a no-show raises `overdue` and a rebooking ladder |
| Consent | no ledger; outreach continues | consent-aware: withdrawal freezes all outreach and flags a human review |
| Idle cases | nobody notices | watchdog flags any open case with no events for N days (ignores a confirmed future appointment) and the coordinator acts |
| Scheduler faults | n/a | `orchestration.timer_miss_prob`: the scheduler silently fails to arm a timer; only the watchdog catches it |
| Closure | clinician closes if and when someone remembers | same rule, enforced: `close` needs `evidence_ref`, `signed_by` and a human actor; the scheduler/watchdog can never close; sign-off has an SLA and a ladder |

Pathway events (`acknowledge`, `accept_ownership`, `contact_patient`, `patient_acknowledge`, `book_confirmatory`,
`complete_confirmatory`, `clinician_review`, `close`) go through `app.state_machine.apply`. Side events (`overdue`,
`escalated`, `handoff_requested/accepted/rejected`, `consent_updated`, `watchdog_flagged`, `lost_contact`, `reopened`, plus
bookkeeping events such as `message_sent`, `delivery_failed`, `contact_recovered`, `appointment_missed`, `appointment_rebooked`,
`provider_switched`, `duplicate_result_merged`, `conflicting_ack_rejected`) are typed overlay events: the pathway YAML defines no
transitions for them, so they do not change the pathway state. `reopened` is the only way out of `closed` and moves the case
back to `reopen.resume_state`. A second, conflicting ownership claim in the orchestrated arm is offered to the state machine and
rejected by it (`InvalidTransition`), which is what produces `conflicting_ack_rejected`.

### Messy cases (`messy.py`)

Injected at configurable rates, identically in both arms: wrong contact number (`wrong_contact_rate`), patient switches
provider mid-pathway (`patients.provider_switch_prob`), duplicate result from a second lab, conflicting ownership
acknowledgements, consent withdrawn after booking. `messy.rate_multiplier: 0` switches off the last three.

### Metrics (`metrics.py`)

Per arm: time to ownership (median, p90; `n/a` for the baseline, which has no owner by design), time to first contact, time to
safe closure (median, p90, among closed cases), share open at 30/60/90 days, completion within 90/180/360 days, reopen rate,
escalation precision (escalations followed by any pathway action within 5 working days / all escalations), automation abstention
rate (`n/a` until the AI cut supplies a hook, see `metrics.AbstentionHook`), lost-to-follow-up and silent-loss rate, share past
the 270-day delay-harm threshold, consent breaches, fairness by language and channel (completion, time to closure, and the max-gap
statistic = largest minus smallest group), and per-messy-case handling. Hand-checked arithmetic: `scenario.py` and
`tests/test_sim_metrics.py`.

## What is and is not modelled

Modelled: ownership and handoff, working-day SLA timers, the escalation ladder, outreach by channel and language, wrong contacts,
no-shows, reminders, drop-out, referral rejection with reasons, consent withdrawal, provider switches, duplicate results, conflicting
claims, scheduler timer faults and the watchdog backstop, reopening after closure.

Not modelled (deliberately, or not yet):
- Shared capacity: endoscopy slots, staff workload and queueing. Cases are independent, so the orchestrated arm's extra
  escalations cost nothing here. The escalation volume is reported (`escalations_per_100_cases`) so the pilot can size it.
- Clinical content: findings, pathology, bowel-prep failure, surveillance. Closure means "documented outcome with evidence and a human sign-off".
- Informed refusal as a closure outcome: the pathway YAML has no such exit, so refusers stay open and are flagged `lost_contact`.
- The AI drafting layer. Every message is `draft_source: template`; the abstention rate is a placeholder hook.
- Friday half-days, per-provider differences, cost, and any effect of orchestration on how fast staff work when not breaching an SLA (conservative: orchestration only helps by catching late or forgotten steps).
- The reopen rate is equal in both arms by construction (a late finding arrives after closure regardless of arm).
- Wrong-contact numbers are modelled as a static patient attribute, not a number that goes stale mid-pathway.

## Parameters

Every parameter lives in [`params.yaml`](params.yaml) as `{value, source, status[, notes]}`. Nothing is hard-coded in
`simulator/sim/`. Statuses: `sourced` and `sourced-adjacent` (from public documents; caveats and sources in each leaf and in
`docs/evidence.md`), `calibrated` (tuned so the baseline reproduces the Mohl 2023 completion curve; each leaf records its previous
placeholder), `assumed` (no public source, `source: TODO`). Loading validates that every leaf has all three fields (a missing
`source` is rejected), and warns once with the number of `assumed` parameters. The full table with sources is printed in
`docs/results/results.md`.

**Change a parameter:** edit the `value` in `params.yaml` (keep `source` and `status` honest), run `make evaluate`.
**Layer a file** without editing the base: `make evaluate PARAMS="simulator/params.yaml my_overrides.yaml"`; later files override
earlier leaves, which is how a research file such as `params.proposed.yaml` is merged. **Add a parameter:** add the leaf to
`params.yaml`, add the field to `ModelParams` in `simulator/sim/params.py` and read it where it is used.

Keys read by other code only: `fit_positivity_rate` (`generate_results.py`); `eligible_age_min/max`, `fit_interval_months`,
`malaffi_integration_assumption`, `gi_referral_*`, `navigation_*` and the `*_integrated_ref` / `colonoscopy_completion_*` keys are
provenance and benchmarks reported in `results.md`, not simulator inputs. `generate_results.py` still hard-codes ages 45-75 while
`eligible_age_min` is 40.

### Calibration

The baseline arm's completion (43% / 51% / 56% at 90 / 180 / 360 days, Mohl 2023) was reproduced by adjusting placeholder
parameters only (marked `calibrated`): outreach response rates, staff delays and forgetting, booking lead time, the share
of patients who eventually act unprompted, and the baseline's rejection-noticing and self-rebooking rates. `results.md` states how
close the baseline gets. The orchestrated arm was not tuned; it lands near the actively managed Kaiser reference (Corley 2017)
because the same placeholders leave few patients unreachable once a human chases them. Its size relative to the baseline
(about 1.6x at 180 days) is larger than the 1.27 risk ratio for patient navigation, so treat the gap as an upper-range illustration.

## How results are reproduced

`evaluate` derives one seed per replication from the base seed (`uuid5(seed, "replication", i)`), builds one cohort per replication and
runs both arms on it. Metrics are averaged over replications; 95% CIs are percentile bootstrap intervals over replications
(1,000 resamples, seeded), and the arm difference is paired by replication. `docs/results/results.json` has sorted keys, floats
rounded to 6 places, and no timestamps, paths or timings; it records the seed, a hash of the effective parameters, a hash of the
pathway YAML, the git commit and the per-replication event-log hashes. `make evaluate-check` proves the same inputs give the same bytes
(including across process counts). `results.md` additionally carries a `generated_at` line.

## Layout

```
simulator/sim/params.py     YAML loader, validation, typed ModelParams
simulator/sim/calendar.py   working-day calendar (date API and simulation-clock API)
simulator/sim/patients.py   synthetic patient attributes
simulator/sim/behaviour.py  pre-drawn latents + behaviour functions
simulator/sim/messy.py      messy-case plans
simulator/sim/engine.py     discrete-event engine, both arms
simulator/sim/invariants.py independent closure-rule and consent checks
simulator/sim/metrics.py    case records and metrics
simulator/sim/scenario.py   hand-built five-case scenario with hand-computed expected metrics
simulator/sim/evaluate.py   replications, bootstrap, results.json
simulator/sim/report.py     results.md
simulator/sim/runner.py     loading and running helpers
simulator/sim/cli.py        python -m simulator.sim.cli run | evaluate
simulator/tests/test_sim_*  determinism, calendar, closure-rule property test, metric arithmetic, params validation, evaluate reproducibility
```

## Cohort pipeline (`make demo-seed`)

Three steps, all synthetic, all seeded (default seed 20261017). Provenance and limitations:
[`docs/data-provenance.md`](../docs/data-provenance.md).

1. **Generate** (`make synthea`): Synthea v3.3.0 runs inside the `synthea` compose container (profile
   `tools`; never on the host) and writes FHIR R4 bundles to `data/synthea/out/fhir/` (gitignored).
2. **Load + results**:
   - `load_cohort.py` POSTs the hospital and practitioner bundles, then every patient bundle, to HAPI
     (`FHIR_BASE_URL`, default `http://localhost:8080/fhir`). It refuses to run if HAPI already has Patients
     (`--force` to override; `--skip-if-loaded` to exit 0 instead).
   - `generate_results.py` reads the patients back, picks those aged 45-75 on `SIM_CLOCK_START`, draws a seeded
     FIT outcome per patient (`fit_positivity_rate` in `params.yaml`), and writes an Observation + DiagnosticReport
     per positive, plus `data/results/manifest.json`. Options: `--seed`, `--cap N`, `--rate`.
3. **Cases**: `create_cases.py` POSTs one case per manifest entry to `API_URL/cases` (default
   `http://localhost:8000`) with a deterministic id (uuid5 of the report id); a 409 `CaseAlreadyExists` means
   already created, so reruns are idempotent.

`make demo-seed` runs all of it (generating only if `data/synthea/out/fhir` is empty). `make demo-seed-clean`
removes `data/synthea/out` and `data/results`. Full reset: `make reset && make demo-seed`.
`make test` includes the offline selection tests in `simulator/tests/` (sample bundle in `simulator/sample/`).
