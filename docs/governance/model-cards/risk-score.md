# Model card: risk score

Long-form record: [risk-score-card.md](../risk-score-card.md) (its generated blocks are checked by `make risk-check`). Template: [README.md](README.md).

## Summary

| Field | Value |
|---|---|
| Model / version id | Logistic regression, coefficient hash `0ce21db6db95c3641c7d5fe0a105c9fdb0b057b692f308a84b88c2c5af62ddf3`, row hash `36d89c2afaef3e03891642fa13636ac6c6bb83f3050cf9f91188ceeb73775d04` (`docs/results/risk/risk.json`, `meta`) |
| Kind | L2-regularised logistic regression, Newton-Raphson, standard-library Python; 9 features |
| Provider | None (in process, no network, no external model) |
| Licence | Repository MIT licence (`LICENSE`) |
| Where in code | `api/app/risk/` (`model.json`, `features.py`, `score.py`, `train.py`); read in one place only, `api/app/routers/cases.py`; trained by `simulator/sim/export_training.py` |
| Owner role | Clinical safety officer (to be named, H1) |

## Intended use

One use: ordering the coordinator worklist. `GET /cases` carries `risk_score` (0 to 1, the modelled probability of no colonoscopy within 180 days of the result); cases past their deadline stay first and the score orders cases within that. `GET /cases/{id}` carries the score and its three strongest factors. Staff roles only; patients get `null`. The agency's `sla_risk_explainer` turns the factors into one sentence for staff.

## Out-of-scope uses

Closing, escalating, changing an SLA, starting any automated action, any care decision, excluding a case from follow-up, patient-facing use, staff performance measurement, resource allocation between providers, and use on real patients before retraining and validation. Enforced in code: `app.risk` is imported only by the cases router and an AST scan fails the build otherwise (`api/tests/test_risk_boundary.py`); a property test shows case logs, read models and worklist order are identical with the real, a constant, a random or no score; no agency plan reads it (T-A66).

**Language is not an input.** It is excluded from the model, from `CaseFeatures` and from `top_factors`; `Model` refuses a `model.json` with a column for an excluded feature (`risk.json` `meta.excluded_features = ['language']`). Language is used only as an audit label.

## Data

Synthetic only, from the behaviour simulator: the orchestrated arm of the literature-anchored scenario, 20,000 cases, seed 20261017, 13,511 snapshot rows, 9,511 train and 4,000 held-out (assigned by a hash of the case key), label "no colonoscopy within 180 days" (`risk.json` `meta`). Most simulator parameters are placeholders (88 of 132 `assumed` per the risk card), so the relationships are the simulator's own assumptions. The age band is a null feature by construction.

## Evaluation

Held-out split, from `docs/results/risk/risk.json` (report: [training.md](../../results/risk/training.md)).

| Measure | Value | Field |
|---|---|---|
| AUC | 0.9065 | `heldout.auc` |
| Brier / base-rate Brier / skill | 0.1208 / 0.2499 / 0.5165 | `heldout.brier*` |
| ECE (tolerance at most 0.05) | 0.0264, tolerance met | `heldout.ece`, `tolerance.met` |
| As served today (channel and age unknown): AUC / Brier / ECE | 0.9058 / 0.1212 / 0.0271 | `heldout_attributes_unknown` |
| Chance-level ECE at this sample size (mean, 95th percentile) | 0.0142, 0.0206 | `noise_floor` |

Gate: `make risk-check` fails if a fresh retrain differs from the committed files (T-D12); the ECE tolerance of 0.05 is in `tolerance.ece_max`.

## Limitations

- Learned the simulator, not patients: do not quote the numbers as operational performance.
- A linear-logit model cannot represent the interaction between stage and days; calibration gaps sit in the 0.5 to 0.8 bins (risk card section 5).
- Channel and age band are unknown at serving today, so the served score rests on stage, days since result, escalations, bounces, consent withdrawal, provider change and the contact flag.
- It is only an aid for ordering: a low score never makes a case safe to leave.

## Subgroup performance

From [fairness.md](../../results/fairness.md), which reads `docs/results/risk/risk.json` (audit groups; language is not an input):

| Subgroup | Held-out n | AUC | ECE | Predicted minus observed | Flag |
|---|---|---|---|---|---|
| language=ar | 1846 | 0.916 | 0.035 | +0.008 | watch: ECE above its noise p95 (0.027) |
| language=en | 1351 | 0.891 | 0.033 | +0.009 | ok |
| language=other | 803 | 0.906 | 0.037 | +0.001 | ok |
| channel (4 groups) | 603 to 1586 | 0.901 to 0.910 | 0.027 to 0.043 | -0.002 to +0.012 | ok |
| age band (4 groups) | 648 to 1125 | 0.893 to 0.912 | 0.030 to 0.033 | -0.006 to +0.018 | ok |

Largest AUC gap 0.025 (English below Arabic), under the 0.05 flag line; no calibration-in-the-large flag. Intersections (language by channel) are not reported.

## Human oversight

The score changes order, never state. Every open case keeps its owner, timers and escalation ladder regardless of score; closure needs a clinician's sign-off with evidence. Staff see the top factors and can disregard the order.

## Monitoring

Exists in code today: `make risk-check` and the risk-boundary tests in CI; a warning log "risk score unavailable" when scoring fails (`api/app/routers/cases.py`). No runtime drift metric exists for the score. Proposed: compare the live distribution of `risk_score` and the observed loss rate per language on shadow-mode data once real data exists, using the same tolerances (ECE at most 0.05, per-group gap at most 0.02). Incident handling: [ai-incidents.md](../ai-incidents.md).

## Kill switch

There is no environment flag. A model that cannot be loaded leaves `risk_score` null and the worklist unaffected (risk card section 2). To switch it off, deploy with `api/app/risk/model.json` removed or invalid and restart the API; the worklist then sorts by deadline only (to verify on the deployed stack; no automated test of the removal itself is cited here).

## Owner and review

Owner: Clinical safety officer (to be named, H1). Review cadence: on every retrain and at least every six months. Last reviewed: 2026-10-25. Reviewed by: AI-assisted engineering review only; no clinician or native-Arabic review yet.

## Changelog

- 2026-10-25: card created in the uniform template.
- Earlier history (D12 language removal, B18 recalibration) is in risk-score-card.md section 6.
