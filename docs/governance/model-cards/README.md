# Model cards

One card for each AI component or AI fallback in the screening-closure system. They sit beside the older design records ([reply-triage-card.md](../reply-triage-card.md), [risk-score-card.md](../risk-score-card.md)), which stay the long-form sources; the cards here are the short, uniform view a reviewer, a DoH assessor or an on-call engineer reads first. The overall framing is in [responsible-ai.md](../responsible-ai.md); incidents and kill switches are in [ai-incidents.md](../ai-incidents.md).

**Status of everything below.** All models were trained or tested on synthetic data. No card claims clinical validation, regulatory approval or use on real patients. Every card is last reviewed 2026-10-25 by AI-assisted engineering review only; no clinician or native-Arabic review has happened yet (H1 and H2 are open).

## The template

Every card uses exactly these headings in this order. A card that cannot fill a section says so, and marks anything it cannot confirm from code or committed artefacts "to verify".

| Heading | What goes there |
|---|---|
| `## Summary` | One table: model or version id, kind, provider, licence, where in code, owner role. |
| `## Intended use` | The one job the component does, who sees its output, and what happens to it next. |
| `## Out-of-scope uses` | What it must not be used for, and where the code or policy enforces that. |
| `## Data` | Training and evaluation data and provenance; say plainly where all of it is synthetic. |
| `## Evaluation` | Metrics, each with the committed file it comes from. No number without a source. |
| `## Limitations` | Known weaknesses, failure modes and what has not been tested. |
| `## Subgroup performance` | Per-language (and other group) evidence, linking [fairness.md](../../results/fairness.md). |
| `## Human oversight` | What a person must still decide or check, and where in the product. |
| `## Monitoring` | Which metrics and alerts watch it, which exist in code today, and a link to [ai-incidents.md](../ai-incidents.md). |
| `## Kill switch` | How to turn this one component off and what the system then does. |
| `## Owner and review` | A named role (not a person), review cadence, last-reviewed date, who reviewed. |
| `## Changelog` | Dated list of changes to the component or the card. |

## Index

| Card | Kind | Can do | Cannot do | Owner role |
|---|---|---|---|---|
| [reply-triage](reply-triage.md) | Fine-tuned Qwen2.5-1.5B LoRA classifier, offline | Label a patient reply with one of nine intents and a calibrated confidence; flag the case for a callback or a review | Reply to a patient, change consent, move or close a case; it never handles `opt_out`, `question` or `other` on its own | Clinical safety officer (to be named, H1) |
| [risk-score](risk-score.md) | Logistic regression, pure Python | Order the coordinator worklist within a deadline band; explain the top factors | Close, escalate, change an SLA, start an action, rank by language, or be shown to patients | Clinical safety officer (to be named, H1) |
| [claude-drafting](claude-drafting.md) | Claude Haiku via API, replay cache by default | Propose one patient message text for a person to approve | Send anything, see patient identifiers or results, state a finding; every draft passes the validator and a human | Clinical safety officer (to be named, H1) |
| [claude-planner](claude-planner.md) | Claude Sonnet via API, replay cache by default | Order the agency department heads' candidate tasks and write a short rationale | Add, drop or change a task, change case state, or see patient text | Programme lead (to be named) |
| [template-fallback](template-fallback.md) | Deterministic templates, no model | Produce the reviewed message text that every AI path falls back to | Adapt to the patient, answer a question or draft anything outside the template matrix | Clinical safety officer (to be named, H1) |

Changing any of these components (new model, new adapter, new prompt, new thresholds, retraining) ships through a pull request that updates its card, per the retraining policy in [ai-incidents.md](../ai-incidents.md).
