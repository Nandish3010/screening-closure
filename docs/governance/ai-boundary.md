# AI boundary

Where a language model may be used in this system, where it may not, how its output is checked, and how we count the times we did not use it. Design rationale: ADR-001 and ADR-007 in `project/DECISIONS.md`. Code: `api/app/ai/`.

**One sentence.** Models are used in four places, drafting patient messages for human approval, ordering the worklist by a calibrated risk score, planning the agency's work queue, and classifying inbound patient replies, plus automatic sending of clinician-pre-approved deterministic templates; no model and no agent can move a case between states, approve a message, change consent or close a case.

The sections below set out the drafting model's boundary in full.

## 1. What the model may do

- Draft one patient message (purpose, channel and language chosen by a person or by the pathway), as text.
- Nothing else. There is no model call anywhere in the closure engine, the scheduler, the escalation ladder, the watchdog or the closure check.

A draft is only a proposal. It is shown to the case owner, who approves it or rejects it. Only after approval does the notification mock "send" it. The rule is enforced by the case's state machine, not only by the route: it accepts a `message_sent` only for a draft a human approved on that case, once, and only if the patient still consents to that channel when it is applied (ADR-030). The exact prompt, the draft, the provider and model, the requester, the approver and the timestamps are stored as Provenance (D4, D5): see section 7.

The model sees the minimum it needs: the care owner's name and role, the next step in plain words, an appointment date if there is one, and the pathway name. It is never given the patient's name, contact details, identifiers or clinical history. No message may state or hint at what the screening found (section 4a).

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
| `azure_openai`, `jais` | HTTP adapters (`api/app/ai/adapters.py`) wrapped by the output cache (`api/app/ai/cache.py`). Network calls are off by default (`LLM_NETWORK_ENABLED=false`). Tests and the default configuration never call a real endpoint. |
| anything else | Startup fails with a clear error (`UnknownProvider`). |

**Switches and keys.** Environment variables only. Real values never go in the repository, and these keys are deliberately not in `.env.example`.

| Variable | Meaning | Default |
|---|---|---|
| `LLM_PROVIDER` | `template`, `azure_openai` or `jais` | `template` |
| `LLM_NETWORK_ENABLED` | `true` allows outbound calls to the model endpoint. Anything else keeps the network off: the adapter raises `ProviderUnavailable` before building a request. | `false` |
| `LLM_CACHE_MODE` | `replay`, `record` or `off` (section 3a) | `replay` |
| `LLM_CACHE_DIR` | Cache directory | `api/app/ai/cache/` |
| `LLM_TIMEOUT_SECONDS` | Per-request timeout, 0 to 120 | `10` |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | Required for `azure_openai`. Request goes to `<endpoint>/openai/deployments/<deployment>/chat/completions` with the key in the `api-key` header. | none |
| `AZURE_OPENAI_API_VERSION` | Optional API version | `2024-06-01` |
| `JAIS_ENDPOINT`, `JAIS_API_KEY`, `JAIS_MODEL` | Required for `jais`. The endpoint is the full URL of an OpenAI-compatible chat completions API; the key is sent as a bearer token. The hosting and API shape are an assumption to confirm with the host. | none |

Adapter rules: the endpoint must be `https` (plain `http` only for `localhost`); the key is never logged or put in an error message; temperature 0; a timeout, connection error, non-200 status, malformed body or cut-off output is a failure that becomes a template draft. The request carries a fixed system prompt and the user prompt built from the request (section 7) and nothing else: no patient data.

**Prompt.** The system prompt forbids diagnosis, naming any disease, medical advice and reassurance; asks for plain language at about grade 6; asks for the exact language requested; tells the model to use the care owner name, next step and appointment date word for word; and asks for the message text only. The validator in section 4 still runs on whatever comes back.

### 3a. Output cache (record and replay)

Model providers are wrapped in a cache keyed by the prompt hash, so the demo runs offline and repeats exactly.

| `LLM_CACHE_MODE` | Behaviour |
|---|---|
| `replay` (default) | Never touches the network. A recorded output for the prompt is used. With none, the template is used and the abstention reason is `cache_miss`. |
| `record` | Calls the real provider (still needs `LLM_NETWORK_ENABLED=true`) and stores the output, even one the validator will reject, so a replay reproduces the same outcome. |
| `off` | No cache. The provider is called every time. |

Files are `api/app/ai/cache/<provider>/<prompt_hash>.json`, each holding the model id, the language, the text and the exact prompt. The directory is committed for the demo. It is empty until someone with credentials records it: `LLM_PROVIDER=azure_openai LLM_NETWORK_ENABLED=true python -m app.ai.cache record` (the care owner's name is part of the prompt, so record with the demo owner's name: `--owner-name`). The template provider needs no cache.

## 4. Output validation

Every model draft is checked by `validate(text, request)` before anyone sees it. All rules run and all failures are reported. A draft passes only if it breaks none of them.

| Rule | Check |
|---|---|
| `empty` | Text is not blank. |
| `unfilled_placeholder` | No braces, `[...]` or `<...>` left over. |
| `too_long` | At most 320 characters for SMS, 600 for WhatsApp, 1200 for a letter. |
| `wrong_language` | Every letter must be in the language's script: Arabic letters only for Arabic, Latin letters only for English. There is no ratio, because one Cyrillic or Greek letter among Latin ones is how a look-alike word is made (SEC-1). Names, the next step and the date supplied in the request are ignored for this check, and are removed only where they stand alone as a whole token (an owner named "Om" does not hide the "om" of `evil.com`). |
| `invisible_character` | No character of Unicode category Cf (zero-width space and joiners, bidi marks and overrides, soft hyphen, tag characters), Cc (controls, tab and carriage return included), Co (private use), Cs, Cn, Zl or Zp. The one exception is the line feed, which the WhatsApp and letter templates use for paragraphs (SEC-1). |
| `missing_owner_name`, `missing_next_step` | The care owner's name and the next step appear in the text. An appointment confirmation must also contain the appointment date (`missing_appointment_date`). |
| `forbidden_phrase:<id>` | None of the phrases in `api/app/ai/forbidden_phrases.yaml`: diagnosis statements ("you have", "you are ill"), disease terms (cancer, tumour, malignant, polyp), the word "positive" as a result, result-disclosure phrases ("found blood", "blood in your stool", "positive result", "abnormal result", section 4a), false reassurance ("do not worry", "it is probably nothing", "you will be fine"), clinical judgement ("urgent", "serious"), medication and treatment, and advice ("you should", "we recommend", diet, fasting). Both the English and the Arabic list are applied to every draft; the English list is matched on a confusables skeleton of the text, which folds look-alike Cyrillic and Greek letters to Latin and drops hidden characters, so "cаncer" with a Cyrillic "а" is still "cancer" (SEC-1). Each entry has a rationale and a tested example. |
| `pii_digits` | No run of 7 or more digits, however punctuated (spaces, dashes, dots, brackets, `/` and `,`), in any script's decimal digits (Arabic-Indic, Devanagari, fullwidth...) (SEC-1). |
| `pii_email` | No `@`. |
| `pii_identifier` | No identifier words (MRN, Emirates ID, passport, file number and Arabic equivalents) and no letter-and-digit codes of 6 or more characters. |
| `url` | No links, domains or messaging short links. |

The rules are intentionally strict. A false rejection costs one template message. A false accept could put a diagnosis, a reassurance or an identifier in front of a patient. The phrase lists are keyword filters: they reduce risk and are tested, but they do not prove a message is clinically safe. That is why a human approves every draft.

The phrase lists and the Arabic text are pending clinician review (H1) and native-speaker review (H2). Until then the Arabic is labelled an unreviewed draft.

## 4a. Non-disclosure of the screening result (E14)

**The rule.** No outbound patient message (SMS, WhatsApp or letter, any purpose, either language) and no line on the patient page states or hints at what the screening found. Reason, in the validator's own words: IFHAS: abnormal results are communicated with the participant present (IFHAS V10, summarised in `docs/evidence.md` section 8b). A text message is not the participant present, so the result is given in person or by phone by the care team.

**What a message may say.** That the screening result is ready; that the care team needs to talk with the patient about the next step; who the care owner is and that they will call (or that the patient can reply to book a time); and, for an appointment, the appointment itself, named as an appointment and never as a disease or a finding ("an appointment for a camera test"). The first message reads: "Your screening result is ready and your care team needs to talk with you about the next step. {owner_name} will call you, or you can reply to book a time. The next step is {next_step}." Reminders, appointment confirmations and escalation notices follow the same pattern. The patient page adds the line "Your care team will explain your result in person or by phone." (English and Arabic). The colonoscopy may be named as the procedure on the page and in a message's `{next_step}`, because the standard concerns the result, not the procedure; the default next step in a message is "a camera test".

**How it is enforced.** The category `result_disclosure` in `api/app/ai/forbidden_phrases.yaml` (English and Arabic) rejects a model draft containing, for example, "found blood", "blood in your stool", "positive result", "abnormal result", "test was positive", "your result shows", and their Arabic equivalents. The same entries cover the existing "positive" jargon rule. A rejected draft becomes the template, which is tested (every purpose, channel and language) to pass the validator and to contain none of these phrases. The patient page strings are tested for the same words in `web/tests/patient.test.tsx`.

**Clinician question (H1).** Confirm the non-disclosure wording: is the first message acceptable as written, is naming the next step ("a camera test") acceptable, and what should the care team do when a patient replies asking for the result by text? The question is in the clinician pack (`docs/review/clinician-pack.md`, "No result in any text"). The Arabic wording is pending native-speaker review (H2).

## 5. Fallback behaviour

`draft_with_fallback(request, provider)`:

1. Ask the provider for a draft.
2. If the provider raises (unavailable, timeout, any error), or returns a result for a different language, or the text fails validation, return the template text for the same purpose, channel and language instead. The result has `fallback_used = true`, `provider = template` and an `abstention_reason`.
3. Record the abstention (section 6).
4. Never raise to the caller because of a provider problem. A draft is always produced.

Abstention reasons: `provider_unavailable`, `cache_miss`, `provider_error:<ExceptionName>`, `invalid_result`, and `validation_failed:<rule>[,<rule>...]`.

The template is the reviewed text the owner would otherwise write from. Every template for every purpose, channel and language is rendered and validated by the tests, so the fallback itself always passes the rules. When `LLM_PROVIDER=template` there is no model route, so there is nothing to abstain from and nothing is counted as an abstention.

## 6. Abstention metric

**Definition.** The automation abstention rate is the share of draft attempts in which the system did not use the model's output.

    abstention rate = abstentions / draft attempts

- A draft attempt is one call to `draft_with_fallback`.
- An abstention is an attempt that ended in the template because the provider failed or its output was rejected. It is recorded with the purpose, channel, language, provider, reason and prompt hash.
- Drafts produced by the `template` provider are attempts but not abstentions.

It is reported overall and broken down by reason, by validation rule, by language and by channel (the fairness split in the evaluation). A high rate for one language or channel shows where the model is weaker, and the care owner still receives a usable draft either way. The counts come from the stored drafts (below), not from process memory, so they survive a restart. The in-memory `AbstentionLog` still exists for code that drafts outside the API.

**Where it is stored and read (D11).** Every `POST /cases/{id}/drafts` writes one `drafts` row. A row whose `abstention_reason` is set (with `abstained_at`, the simulation time) is an abstention. `GET /metrics/abstention` (coordinator and supervisor) returns `{attempts, abstentions, rate, by_reason, by_language, by_channel}`: `by_reason` counts abstentions by reason category (`validation_failed`, `provider_unavailable`, `provider_error`, `invalid_result`, `cache_miss`); the other two are the fairness split. `app.metrics_abstention.abstention_rate_hook` is the function the simulator's `AbstentionHook` calls: it runs one draft per case record, in the record's language and channel, through the configured provider and cache and returns the abstention share (0.0 for `template`, 1.0 for a model provider with an empty cache).

## 7. Provenance

Each draft is stored with what is needed to reproduce and audit it (`api/migrations/007_drafts.sql`):

- `drafts`: draft id, case, purpose, channel, language, text, provider, model id, whether the template was used, abstention reason and time, prompt hash, who requested it (`created_by`), when (simulation time), and the one human decision on it: `approved_at` / `approved_by` (filled by `POST /drafts/{id}/approve`, D5), or `rejected_at` / `rejected_by` / `rejection_reason` (filled by `POST /drafts/{id}/reject`; `api/migrations/008_draft_decisions.sql`). Both are empty while the draft waits for the owner, and a draft can have only one of the two.
- `prompts`: the prompt hash with the exact system prompt and user prompt it covers. `prompt_hash = sha256(system_prompt + "\n---\n" + prompt_text)`, so an auditor recomputes the hash from the stored text and re-runs the same prompt.
- The `message_drafted` event on the case carries the draft id, the provider, `fallback_used`, the prompt hash and the language, with the actor `model:<provider>`, and never the text (the strict payload schema of A14 refuses it; the text is read from the `drafts` table). It is an annotation: it changes no state. Approval writes `message_approved` (only the approve route can, T-D1) and the notification mock then writes `message_sent`. A rejection writes no event yet (follow-up A16).

The database refuses to edit or delete a prompt, to delete a draft, and to change anything on a draft except filling its one decision (approval or rejection) once. A draft is refused (HTTP 409) for a case whose outreach is frozen by a consent withdrawal, and for a closed case.
