import { useEffect, useState } from "react";
import { ApiError, type AccessLog } from "../api/contract";
import { useApp } from "../app/AppContext";
import { fmtDateTime } from "../lib/format";

/** What was read, in staff words (the API's fixed `kind` vocabulary). */
const KIND: Record<string, string> = {
  case: "Opened the case page", timeline: "Opened the timeline", messages: "Read the messages sent", fhir: "Read the FHIR record ids", replies: "Read the patient's replies",
};

/**
 * "Who has looked at this record" (access audit, ADR-053): the staff reads the patient is shown in their privacy centre, with the
 * extra detail staff get (the actor and what was read). A read is written once per person and kind per 15 minutes. Reading this
 * list is not itself written. Not every role may read it (owners only on their own case, not the lab): a refusal is said, not hidden.
 */
export function AccessLogPanel({ caseId }: { caseId: string }) {
  const { api } = useApp();
  const [log, setLog] = useState<AccessLog | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setLog(null);
    setRefused(null);
    api.getAccessLog(caseId).then(
      (l) => live && setLog(l),
      (e) => live && setRefused(e instanceof ApiError && e.status === 403 ? "Your role cannot read the access log of this case." : "The access log could not be loaded."),
    );
    return () => {
      live = false;
    };
  }, [api, caseId]);
  return (
    <section className="panel" aria-labelledby="access-h" data-testid="access-audit">
      <h2 id="access-h">Who has looked at this record</h2>
      <p className="muted">
        Each time a member of staff opens this case it is written here, and the patient sees the same list (role, name and time) in their privacy centre.
        One line per person and kind of read every {log?.window_minutes ?? 15} minutes. Automatic services and the worklist are not listed.
      </p>
      {refused ? (
        <p className="muted" role="status">{refused}</p>
      ) : !log ? (
        <p className="muted" role="status">Loading...</p>
      ) : log.entries.length === 0 ? (
        <p className="muted" data-testid="access-audit-empty">Nobody has opened this record yet.</p>
      ) : (
        <table className="worklist">
          <caption className="visually-hidden">Staff who opened this record, newest first</caption>
          <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Role</th><th scope="col">What</th></tr></thead>
          <tbody>
            {log.entries.map((e, i) => (
              <tr key={`${e.at}-${e.actor ?? e.name}-${e.kind ?? i}-${i}`} data-testid="access-audit-row">
                <td><time dateTime={e.at}>{fmtDateTime(e.at)}</time></td>
                <td>{e.name}</td>
                <td>{e.role.replace(/_/g, " ")}</td>
                <td>{KIND[e.kind ?? ""] ?? e.kind ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
