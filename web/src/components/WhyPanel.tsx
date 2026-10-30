import { abstentionText } from "./DraftsPanel";
import { DOCS, docHref, type DocLink } from "../judge/links";

/**
 * Reply-triage confidence threshold. Source of truth: api/app/triage/calibration.json ("threshold"): a reply is handled without a
 * person only at or above it, and only for the intents listed there as auto-handled.
 */
export const TRIAGE_THRESHOLD = 0.553;
const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

export interface WhyContent {
  model: string;
  confidence: string;
  rule: string;
  human: string;
  card: DocLink;
}

export type WhyInput =
  | { kind: "draft"; provider: string | null | undefined; model_id?: string | null; fallback_used: boolean; abstention_reason?: string | null }
  | { kind: "triage"; model_id?: string | null; confidence?: number | null; needs_human?: boolean | null; forced_reason?: string | null }
  | { kind: "risk" }
  | { kind: "planner"; provider: string | null | undefined; model_id?: string | null; fallback_used: boolean | null | undefined };

const isTemplate = (provider: string | null | undefined, fallback: boolean | null | undefined) =>
  fallback === true || (provider ?? "").toLowerCase() === "template";

/** The language-group rule (api/app/triage/dialect.py, hazard HZ-43) that sends a reply to a person, in plain words. */
const GROUP_RULE: Record<string, string> = {
  ar_gulf: "Gulf dialect: a person always checks these replies (safety rule HZ-43). The audit found the model too often wrong on them, so no confidence is high enough. ",
  arabizi: "Arabic in Latin letters: a person checks these replies unless the model is very sure (safety rule HZ-43). ",
};

/** Pure: what the "Why?" panel says for one AI output. */
export function whyContent(i: WhyInput): WhyContent {
  if (i.kind === "draft") {
    const template = isTemplate(i.provider, i.fallback_used);
    return {
      model: template ? "Template (no AI)" : [i.provider, i.model_id].filter(Boolean).join(" / ") || "Model not recorded",
      confidence: "not applicable",
      rule:
        "Passed the forbidden-phrase validator. If the model abstains or fails validation, the approved template is used." +
        (i.abstention_reason ? ` This draft: ${abstentionText(i.abstention_reason)}.` : ""),
      human: "Approve or reject before anything is sent.",
      card: template ? DOCS.cardTemplate : DOCS.cardDrafting,
    };
  }
  if (i.kind === "triage") {
    const none = !i.model_id || i.model_id === "unavailable";
    return {
      model: none
        ? "No model available. Sent to a person."
        : i.model_id!.startsWith("rule:")
          ? "Opt-out rule (no AI), checked before any model"
          : i.model_id!,
      confidence: typeof i.confidence === "number" ? pct(i.confidence) : "not available",
      rule: `${GROUP_RULE[i.forced_reason ?? ""] ?? ""}Handled without a person only if confidence is at least ${pct(TRIAGE_THRESHOLD, 1)} and the intent is not question, other or opt-out. Otherwise a person reads it.${i.needs_human ? " This reply is waiting for a person." : ""}`,
      human: "Review the reply. An opt-out is always read by a person.",
      card: DOCS.cardTriage,
    };
  }
  if (i.kind === "risk") {
    return {
      model: "Logistic regression on the case record (no external AI provider)",
      confidence: "Risk score shown as a percentage, 0 to 100% (modelled chance of no follow-up test within 180 days)",
      rule: "Orders the worklist only; never acts. Language is not an input.",
      human: "Decide which case to work first. The score does not change any due date or escalation.",
      card: DOCS.cardRisk,
    };
  }
  const template = isTemplate(i.provider, i.fallback_used);
  return {
    model: template ? "Template (no AI)" : [i.provider, i.model_id].filter(Boolean).join(" / ") || "Not recorded",
    confidence: "not applicable",
    rule: "Agents prepare tasks; they cannot change case state. Anything that needs a decision goes to the needs-human inbox.",
    human: "Accept or dismiss each task.",
    card: template ? DOCS.cardTemplate : DOCS.cardPlanner,
  };
}

/**
 * Coordinator-only disclosure that says what produced an AI output, how sure it was, the rule applied, and that a person still
 * decides. Collapsed by default. Never rendered on the patient page.
 */
export function WhyPanel({ input, subject, testId }: { input: WhyInput; subject: string; testId: string }) {
  const c = whyContent(input);
  return (
    <details className="why-panel" data-testid={testId}>
      <summary data-testid={`${testId}-summary`}>Why? ({subject})</summary>
      <dl>
        <div><dt>Model</dt><dd data-testid={`${testId}-model`}>{c.model}</dd></div>
        <div><dt>Confidence</dt><dd data-testid={`${testId}-confidence`}>{c.confidence}</dd></div>
        <div><dt>Rule applied</dt><dd data-testid={`${testId}-rule`}>{c.rule}</dd></div>
        <div><dt>A human must still decide</dt><dd data-testid={`${testId}-human`}>{c.human}</dd></div>
      </dl>
      <p><a href={docHref(c.card)} target="_blank" rel="noreferrer">{c.card.label}</a> <span className="muted mono">({c.card.path})</span></p>
    </details>
  );
}
