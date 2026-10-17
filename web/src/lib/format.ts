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
