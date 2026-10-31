export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)} UTC`;
}
export function fmtDate(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "-";
}
export function shortId(id: string): string {
  return id.slice(0, 8);
}
export function titleCase(s: string): string {
  const t = s.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** The last characters of a reference, so a reader can tell two records apart: `…4bec2a`. */
export function shortRef(ref: string): string {
  const name = ref.slice(ref.lastIndexOf("/") + 1);
  const id = (name.split("-").pop() ?? name).replace(/[^A-Za-z0-9]/g, "");
  return `\u2026${id.slice(-6)}`;
}

const REF_WORDS: [RegExp, string][] = [
  [/^Procedure\/colonoscopy-/, "the colonoscopy record"],
  [/^DiagnosticReport\/colonoscopy-report-/, "the colonoscopy report"],
  [/^DiagnosticReport\//, "the report"],
  [/^Procedure\//, "the procedure record"],
  [/^Patient\//, "the patient record"],
  [/^Practitioner\//, "the clinician"],
  [/^Appointment\//, "the appointment"],
];

/** One reference in plain words with its visible short id: `the colonoscopy record (…3bfc758)`. */
export function plainRef(ref: string): string {
  const word = REF_WORDS.find(([re]) => re.test(ref))?.[1] ?? "the record";
  return `${word} (${shortRef(ref)})`;
}

/** Record references in a sentence in plain words, each with a visible short id. The raw text stays in a tooltip where it is shown. */
export function plainRefs(s: string): string {
  return s
    .replace(/\b(?:Procedure|DiagnosticReport|Patient|Practitioner|Appointment)\/[\w.-]+/g, (m) => plainRef(m))
    .replace(/\bevidence_ref\b/g, "evidence reference");
}

/** An engine refusal in plain words but with every reference kept exactly as it was submitted (it is what the person typed). */
export function plainError(s: string): string {
  return s.replace(/\bevidence_ref\b/g, "evidence reference");
}
