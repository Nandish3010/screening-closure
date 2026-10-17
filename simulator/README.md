# Behaviour simulator

Status: the synthetic cohort pipeline exists (below). The behaviour simulator itself (driving cases
through the pathway) is still a placeholder.

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

The simulator will drive synthetic cases through the pathway using a seeded random
generator and the injectable sim clock, so that a 45-day pathway runs in minutes and
every number in the documentation can be regenerated with `make evaluate`.

It will model, per case: how long staff take to acknowledge, whether the patient is
reachable (wrong contact details), whether the patient attends (no-show by channel),
and whether the referral is rejected. A baseline arm (no ownership, no clocks) is
compared with the orchestrated arm.

## Parameters

Values live in [`params.yaml`](params.yaml). Every parameter must carry a source
before the submission; until then it is declared `assumed`.

| parameter | value | source | status |
|---|---|---|---|
| acknowledgement_delay_days | 2 | TODO | assumed |
| no_show_rate_by_channel.sms | 0.30 | TODO | assumed |
| no_show_rate_by_channel.phone_call | 0.20 | TODO | assumed |
| no_show_rate_by_channel.letter | 0.40 | TODO | assumed |
| referral_rejection_rate | 0.05 | TODO | assumed |
| wrong_contact_rate | 0.08 | TODO | assumed |
| fit_positivity_rate | 0.07 | TODO (published FIT positivity ranges ~5-8%) | assumed |
