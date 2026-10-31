import { pathwayMeta } from "../lib/pathway";

/**
 * The pathway a case runs on, as a small badge: "Bowel screening", "Cervical screening". The API puts `pathway` on every case
 * row; an unknown name is shown as-is. `title` carries the programme, and, for a pathway whose clinical timings are placeholders,
 * says so (nothing here is a clinical statement).
 */
export function PathwayBadge({ pathway, testId = "pathway-badge" }: { pathway?: string | null; testId?: string }) {
  const meta = pathwayMeta(pathway);
  const title = meta.placeholderTimings ? `${meta.programme}. Timings are placeholders pending clinician sign-off (H1).` : meta.programme;
  return (
    <span className={`chip chip-pathway chip-pathway-${meta.name}`} data-testid={testId} data-pathway={meta.name} title={title}>
      {meta.label}
    </span>
  );
}
