import type { CasePriority } from "../api/contract";
import { PRIORITY_PLACEHOLDER_NOTE, priorityIsPlaceholder, priorityLabel, priorityReason } from "../lib/priority";

/**
 * A case's result priority as a small chip: "Urgent", "Soon", "Routine". `title` carries the reason and, while the threshold is a
 * placeholder, says so (nothing here is a clinical statement). Renders nothing when the case has no priority.
 */
export function PriorityBadge({ priority, testId = "priority-badge" }: { priority?: CasePriority | null; testId?: string }) {
  if (!priority) return null;
  const reason = priorityReason(priority);
  const title = priorityIsPlaceholder(priority) ? `${reason}. ${PRIORITY_PLACEHOLDER_NOTE}.` : reason;
  const tierClass = priority.tier.replace(/[^a-z0-9_-]/gi, "");
  return (
    <span className={`chip chip-priority chip-priority-${tierClass}`} data-testid={testId} data-priority={priority.tier} title={title}>
      {priorityLabel(priority)}
    </span>
  );
}
