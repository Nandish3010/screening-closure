/**
 * What kind of model answered a reply, from its id. Only the local reply-triage model (the fine-tuned Qwen, served on the
 * host) is called "on-premises" (it runs on our server, not on the patient's device); any other id gets the neutral "AI model", because nothing here knows where that one ran.
 */
export type ModelKind = "none" | "rule" | "local" | "other";

export function modelKind(id: string | null | undefined): ModelKind {
  if (!id || id === "unavailable") return "none";
  if (id.startsWith("rule:")) return "rule";
  if (/^qwen/i.test(id)) return "local";
  return "other";
}

/** The model in a few plain words (the result box). The full id belongs under Details. */
export function modelWords(id: string | null | undefined): string {
  switch (modelKind(id)) {
    case "none": return "no model available";
    case "rule": return "opt-out rule, no AI";
    case "local": return "on-premises model";
    default: return "AI model";
  }
}

/** The model for the Why panel's Model row. */
export function modelHeadline(id: string | null | undefined): string {
  switch (modelKind(id)) {
    case "none": return "No model available. Sent to a person.";
    case "rule": return "Opt-out rule (no AI), checked before any model";
    case "local": return "On-premises model (runs on our server, no cloud AI)";
    default: return "AI model";
  }
}
