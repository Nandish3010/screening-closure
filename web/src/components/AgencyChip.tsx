import { useEffect, useId, useState } from "react";
import { useApp } from "../app/AppContext";
import type { CaseAgencySummary } from "../api/contract";
import { AGENCY_DEPARTMENT_LABEL } from "../api/contract";
import { titleCase } from "../lib/format";

/**
 * A22: "agency: 3 tasks, 1 needing you" on the case page. Clicking it toggles the case's agency tasks.
 * Read-only: agents only prepare work, so the list has no action, the case page's own controls are the way to act.
 * Staff roles only; a failed call, a backend without the agency or a case with no agency tasks renders nothing.
 * `version` changes whenever the case changed, so the numbers follow what a person just did.
 */
export function AgencyChip({ caseId, version }: { caseId: string; version: string | number }) {
  const { api, role } = useApp();
  const [data, setData] = useState<CaseAgencySummary | null>(null);
  const [open, setOpen] = useState(false);
  const listId = useId();

  useEffect(() => {
    if (role === "patient") {
      setData(null);
      return;
    }
    let live = true;
    // A client without the agency (an older adapter) throws synchronously: treat it like any other failure.
    void Promise.resolve()
      .then(() => api.caseAgency(caseId))
      .then((x) => live && setData(x))
      .catch(() => live && setData(null));
    return () => {
      live = false;
    };
  }, [api, caseId, role, version]);

  if (role === "patient" || !data || data.total === 0) return null;
  const label = `agency: ${data.total} ${data.total === 1 ? "task" : "tasks"}, ${data.needs_human} needing you`;
  return (
    <div className="agency-chip-row" data-testid="agency-chip-row">
      <button
        type="button"
        className={`chip agency-chip${data.needs_human > 0 ? " chip-warn" : ""}`}
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        data-testid="agency-chip"
      >
        {label}
      </button>
      {open && (
        <ul className="plain agency-chip-list" id={listId} aria-label="Agency tasks on this case" data-testid="agency-chip-list">
          {data.tasks.map((t) => (
            <li key={t.task_id} data-testid="agency-chip-task">
              <strong>{t.title}</strong> <span className="chip">{titleCase(t.status)}</span>
              <span className="muted"> {AGENCY_DEPARTMENT_LABEL[t.department]}</span>
              <div>{t.summary}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
