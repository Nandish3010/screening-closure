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

/** Record references (`Procedure/colonoscopy-<uuid>`) in plain words; the raw text stays in a tooltip where it is shown. */
export function plainRefs(s: string): string {
  return s
    .replace(/\bProcedure\/colonoscopy-[\w-]+/g, "the colonoscopy record")
    .replace(/\bDiagnosticReport\/colonoscopy-report-[\w-]+/g, "the colonoscopy report")
    .replace(/\b(?:Procedure|DiagnosticReport|Patient|Practitioner|Appointment)\/[\w.-]+/g, "the record")
    .replace(/\bevidence_ref\b/g, "evidence reference");
}
