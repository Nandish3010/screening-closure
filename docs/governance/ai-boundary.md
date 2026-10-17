# AI boundary

Where a language model may be used in this system, where it may not, how its output is checked, and how we count the times we did not use it. Design rationale: ADR-001 and ADR-007 in `project/DECISIONS.md`. Code: `api/app/ai/`.

**One sentence.** The model drafts text for a human to approve. It never moves a case, closes a case, decides care or contacts a patient.

## 1. What the model may do

- Draft one patient message (purpose, channel and language chosen by a person or by the pathway), as text.
- Nothing else. There is no model call anywhere in the closure engine, the scheduler, the escalation ladder, the watchdog or the closure check.

A draft is only a proposal. It is shown to the case owner, who approves it or rejects it. Only after approval does the notification mock "send" it. The prompt, the draft, the provider and model, the approver and the timestamps are stored as Provenance (D4, D5).

The model sees the minimum it needs: the care owner's name and role, the next step in plain words, an appointment date if there is one, and the pathway name. It is never given the patient's name, contact details, identifiers or clinical history. The only clinical fact a message may state is that the screening test found blood.

## 2. What the model may not do

| The model may not | How this is enforced |
|---|---|
| Move a case between states | A `model:` actor is automated; the state machine refuses every state-changing event from an automated actor. Only `message_drafted` is allowed (T-D2, D5). |
| Close a case or sign off a closure | `close` needs `evidence_ref` and `signed_by` from a non-automated actor (T-A1, T-A4). |
| Decide or change care, escalate, reassign an owner | These are events with human or scheduler actors; the model has no route to them. |
| Contact a patient directly | There is no code path to `message_sent` except through owner approval (T-D1, D5). The provider returns text and nothing else; it has no handle on any channel. |
| Diagnose, advise or reassure | Output validation rejects it and the template is used instead (section 4). |
| Be required | The default provider is `template`, which needs no network and no model. With the model off or failing, the product behaves the same. |

This is structural, not a policy: the rules above hold even if the model, or a prompt, behaves badly.

## 3. Providers

`LLM_PROVIDER` selects the drafting provider. Default `template`.

| Value | Behaviour |
|---|---|
| `template` | Deterministic, offline. Fills the reviewed template for the purpose, channel and language. Used as the default and as the fallback. |
| `azure_openai`, `jais` | Registered names. Until the adapters (D7) are built they resolve to a stub that raises `ProviderUnavailable`, so a draft falls back to the template. Tests and the default configuration never call a real endpoint. |
| anything else | Startup fails with a clear error (`UnknownProvider`). |

## 4. Output validation

Every model draft is checked by `validate(text, request)` before anyone sees it. All rules run and all failures are reported. A draft passes only if it breaks none of them.

| Rule | Check |
|---|---|
| `empty` | Text is not blank. |
| `unfilled_placeholder` | No braces, `[...]` or `<...>` left over. |
| `too_long` | At most 320 characters for SMS, 600 for WhatsApp, 1200 for a letter. |
| `wrong_language` | Arabic: at least 80% of letters in Arabic script. English: at least 90% Latin. Names, the next step and the date supplied in the request are ignored for this check. |
| `missing_owner_name`, `missing_next_step` | The care owner's name and the next step appear in the text. An appointment confirmation must also contain the appointment date (`missing_appointment_date`). |
| `forbidden_phrase:<id>` | None of the phrases in `api/app/ai/forbidden_phrases.yaml`: diagnosis statements ("you have", "you are ill"), disease terms (cancer, tumour, malignant, polyp), the word "positive" as a result, false reassurance ("do not worry", "it is probably nothing", "you will be fine"), clinical judgement ("urgent", "serious"), medication and treatment, and advice ("you should", "we recommend", diet, fasting). Both the English and the Arabic list are applied to every draft. Each entry has a rationale and a tested example. |
| `pii_digits` | No run of 7 or more digits, including phone-style runs with spaces or dashes and Arabic-Indic digits. |
| `pii_email` | No `@`. |
| `pii_identifier` | No identifier words (MRN, Emirates ID, passport, file number and Arabic equivalents) and no letter-and-digit codes of 6 or more characters. |
| `url` | No links, domains or messaging short links. |

The rules are intentionally strict. A false rejection costs one template message. A false accept could put a diagnosis, a reassurance or an identifier in front of a patient. The phrase lists are keyword filters: they reduce risk and are tested, but they do not prove a message is clinically safe. That is why a human approves every draft.

The phrase lists and the Arabic text are pending clinician review (H1) and native-speaker review (H2). Until then the Arabic is labelled an unreviewed draft.

## 5. Fallback behaviour

`draft_with_fallback(request, provider)`:

1. Ask the provider for a draft.
2. If the provider raises (unavailable, timeout, any error), or returns a result for a different language, or the text fails validation, return the template text for the same purpose, channel and language instead. The result has `fallback_used = true`, `provider = template` and an `abstention_reason`.
3. Record the abstention (section 6).
4. Never raise to the caller because of a provider problem. A draft is always produced.

Abstention reasons: `provider_unavailable`, `provider_error:<ExceptionName>`, `invalid_result`, and `validation_failed:<rule>[,<rule>...]`.

The template is the reviewed text the owner would otherwise write from. Every template for every purpose, channel and language is rendered and validated by the tests, so the fallback itself always passes the rules. When `LLM_PROVIDER=template` there is no model route, so there is nothing to abstain from and nothing is counted as an abstention.

## 6. Abstention metric

**Definition.** The automation abstention rate is the share of draft attempts in which the system did not use the model's output.

    abstention rate = abstentions / draft attempts

- A draft attempt is one call to `draft_with_fallback`.
- An abstention is an attempt that ended in the template because the provider failed or its output was rejected. It is recorded with the purpose, channel, language, provider, reason and prompt hash.
- Drafts produced by the `template` provider are attempts but not abstentions.

It is reported overall and broken down by reason, by validation rule, by language and by channel (the fairness split in the evaluation). A high rate for one language or channel shows where the model is weaker, and the care owner still receives a usable draft either way. The in-memory `AbstentionLog` holds the records; the metrics endpoint and the evaluation read it (D11, T-D15).
