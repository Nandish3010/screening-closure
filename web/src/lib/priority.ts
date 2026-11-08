/**
 * Result priority (ADR-051) on the web. The server derives a tier from a screening result's values and puts `priority` on every
 * staff case row and case page. Priority only ORDERS the worklist and EXPLAINS why a case is where it is: the SLA clock, the
 * escalation ladder and who may close a case do not depend on it. Every threshold behind a tier is a PLACEHOLDER pending
 * clinician sign-off (H1); the Arabic reason text needs native-speaker review (H2).
 */
import type { CasePriority } from "../api/contract";

/** The known tiers, highest first. A pathway may define another code: it sorts after these and is shown as-is. */
export const PRIORITY_TIERS = ["urgent", "soon", "routine"] as const;
export type PriorityTier = (typeof PRIORITY_TIERS)[number];

export const PRIORITY_LABEL: Record<PriorityTier, string> = { urgent: "Urgent", soon: "Soon", routine: "Routine" };

/** Shown wherever the tier is explained, so nobody reads a placeholder threshold as a clinical standard. */
export const PRIORITY_PLACEHOLDER_NOTE = "Threshold is a placeholder pending clinician sign-off";
export const PRIORITY_NOT_SLA_NOTE = "Orders the worklist and explains why. It does not change the SLA clock, the escalation ladder or who may close the case.";

/**
 * Sort key: lower sorts first. A known tier takes its place in PRIORITY_TIERS; a case with NO priority is treated like the
 * lowest known tier ("routine"), so a worklist with no priorities at all keeps its order; an unknown tier code sorts after.
 */
export function priorityRank(p: Pick<CasePriority, "tier"> | null | undefined): number {
  const routine = PRIORITY_TIERS.indexOf("routine");
  if (!p) return routine;
  const i = (PRIORITY_TIERS as readonly string[]).indexOf(p.tier);
  return i === -1 ? PRIORITY_TIERS.length : i;
}

export function priorityLabel(p: Pick<CasePriority, "tier"> | null | undefined): string {
  if (!p) return PRIORITY_LABEL.routine;
  return PRIORITY_LABEL[p.tier as PriorityTier] ?? p.tier.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** The reason the tier was given, in the requested language (English by default; the Arabic needs native review, H2). */
export function priorityReason(p: CasePriority | null | undefined, lang: "en" | "ar" = "en"): string {
  if (!p) return "";
  return (lang === "ar" ? p.reason?.ar : p.reason?.en) || p.reason?.en || p.rule;
}

/** True while the threshold behind the tier is a placeholder (the server's `basis` starts with "PLACEHOLDER"). */
export function priorityIsPlaceholder(p: CasePriority | null | undefined): boolean {
  return !!p && typeof p.basis === "string" && p.basis.startsWith("PLACEHOLDER");
}
