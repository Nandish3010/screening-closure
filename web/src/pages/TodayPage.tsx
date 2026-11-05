import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { Role, TodayActionId, TodayAgencyItem, TodayGroupId, TodayItem } from "../api/contract";
import { ApiError, TODAY_CASE_GROUPS, TODAY_GROUPS } from "../api/contract";
import { ActionButton } from "../components/ActionButton";
import { PathwayBadge } from "../components/PathwayBadge";
import { PriorityBadge } from "../components/PriorityBadge";
import { TODAY } from "../i18n/strings";
import { fmtDateTime, plainError, shortId, titleCase } from "../lib/format";
import { PRIORITY_PLACEHOLDER_NOTE, priorityIsPlaceholder, priorityReason } from "../lib/priority";
import { actionState, isAgencyItem, mergeGroups, replyNotes, whyText, type TodayAnyItem } from "../lib/today";
import { StatePill } from "./CoordinatorPage";

const PAGE = 10;
const STAFF_ROLES: readonly Role[] = ["coordinator", "supervisor"];
type Shown = Record<TodayGroupId, TodayAnyItem[]>;
const EMPTY: Shown = { urgent: [], overdue: [], escalations: [], replies: [], flagged: [], signatures: [], agency: [] };

/** The event each one-click action posts: the same events, payloads and routes the case page uses, so the audit trail is the same. */
function eventFor(action: TodayActionId, item: TodayItem): { type: string; payload: Record<string, unknown> } | null {
  switch (action) {
    case "accept_escalation":
      return { type: "escalation_accepted", payload: { rung: item.escalation?.rung, ...(item.escalation?.milestone ? { milestone: item.escalation.milestone } : {}) } };
    case "take_ownership":
      return { type: "accept_ownership", payload: {} };
    case "confirm_contact":
      return { type: "contact_updated", payload: { contact_valid: true } };
    case "record_callback":
      return { type: "contact_patient", payload: { channel: "phone" } };
    default:
      return null;
  }
}

export function TodayPage() {
  const { api, role, actor, simTime, refreshSim } = useApp();
  const [shown, setShown] = useState<Shown>(EMPTY);
  const [more, setMore] = useState<Record<TodayGroupId, boolean>>({ ...(Object.fromEntries(TODAY_GROUPS.map((g) => [g, false])) as Record<TodayGroupId, boolean>) });
  const [cursor, setCursor] = useState<string | null>(null);
  const [agencyCursor, setAgencyCursor] = useState<string | null>(null);
  const [complete, setComplete] = useState(true);
  const [scanned, setScanned] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [focusGroup, setFocusGroup] = useState<TodayGroupId | null>(null);
  const listHeading = useRef<HTMLHeadingElement | null>(null);
  const [filter, setFilter] = useState<TodayGroupId | "all">("all");
  const [rowNote, setRowNote] = useState<{ key: string; text: string } | null>(null);
  const staff = STAFF_ROLES.includes(role);

  /** Read the first page of every queue again (after an action, a role switch or the clock moving). */
  const load = useCallback(async () => {
    const r = await api.getToday({ limit: PAGE });
    setShown(mergeGroups({}, r.groups));
    setMore(Object.fromEntries(r.groups.map((g) => [g.group, g.more])) as Record<TodayGroupId, boolean>);
    setCursor(r.cursor);
    setAgencyCursor(r.agency_cursor);
    setComplete(r.complete);
    setScanned(r.scanned);
    setError(null);
    setLoaded(true);
  }, [api]);

  useEffect(() => {
    if (!staff) return;
    let live = true;
    // One tick first: the provider's effect that logs in as the chosen role runs after this one, and the request must carry it.
    void Promise.resolve()
      .then(() => (live ? load() : undefined))
      .catch((e) => live && setError(String((e as Error).message ?? e)));
    return () => {
      live = false;
    };
  }, [load, staff, role, simTime]);

  useEffect(() => {
    if (focusGroup && loaded) {
      (listHeading.current ?? document.getElementById("today-empty"))?.focus();
      setFocusGroup(null);
    }
  }, [focusGroup, loaded, shown]);

  const lookFurther = async () => {
    setBusy("scan");
    try {
      const r = await api.getToday({ limit: PAGE, cursor, groups: [...TODAY_CASE_GROUPS] });
      setShown((have) => mergeGroups(have, r.groups));
      setMore((m) => ({ ...m, ...(Object.fromEntries(r.groups.map((g) => [g.group, g.more])) as Record<TodayGroupId, boolean>) }));
      setCursor(r.cursor);
      setComplete(r.complete);
      setScanned((n) => n + r.scanned);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const showMoreAgency = async () => {
    setBusy("agency");
    try {
      const r = await api.getToday({ limit: PAGE, agencyCursor, groups: ["agency"] });
      setShown((have) => mergeGroups(have, r.groups));
      setMore((m) => ({ ...m, agency: r.groups[0]?.more ?? false }));
      setAgencyCursor(r.agency_cursor);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(null);
    }
  };

  const act = async (group: TodayGroupId, item: TodayItem, action: TodayActionId) => {
    const ev = eventFor(action, item);
    if (!ev) return;
    setBusy(`${group}:${item.case_id}:${action}`);
    setFlash(null);
    setError(null);
    setRowNote(null);
    try {
      await api.postEvent(item.case_id, { type: ev.type, actor, payload: ev.payload });
      await load();
      refreshSim();
      setFlash(`${TODAY.done[action as keyof typeof TODAY.done]} (${shortId(item.case_id)})`);
      setFocusGroup(group);
    } catch (e) {
      const msg = e instanceof ApiError ? `The engine refused this: ${plainError(e.message)}` : `Something went wrong: ${(e as Error).message}`;
      setError(msg);
      // Conflict: someone else got there first. Say so on the row, and read the queue again so it shows the truth.
      if (e instanceof ApiError && e.status === 409) {
        setRowNote({ key: `${group}:${item.case_id}`, text: TODAY.conflict[action as keyof typeof TODAY.conflict] ?? TODAY.conflict.default });
        void load().then(() => setError(msg)).catch(() => undefined);
      }
    } finally {
      setBusy(null);
    }
  };

  if (!staff) {
    return (
      <main className="container">
        <div className="page-head"><h1>{TODAY.heading}</h1><span className="chip chip-synthetic">Synthetic data</span></div>
        <p className="banner banner-warn" role="status" data-testid="today-not-for-role">{TODAY.notForRole}</p>
        <p><Link to="/coordinator">{TODAY.fullWorklist}</Link></p>
      </main>
    );
  }

  const countLabel = (g: TodayGroupId) => `${shown[g].length}${more[g] || (!complete && g !== "agency") ? "+" : ""}`;
  const total = TODAY_GROUPS.reduce((n, g) => n + shown[g].length, 0);
  const present = TODAY_GROUPS.filter((g) => shown[g].length > 0);
  const visible = filter === "all" ? present : present.filter((g) => g === filter);
  const searching = !complete && total === 0;

  return (
    <main className="container today">
      <div className="page-head"><h1>{TODAY.heading}</h1><span className="chip chip-synthetic">Synthetic data</span></div>
      <p className="muted">{TODAY.intro}</p>
      {loaded && (
        <p className="today-summary" data-testid="today-summary" role="status">{TODAY.summary(present.map((g) => ({ g, n: shown[g].length, plus: more[g] || (!complete && g !== "agency") })), complete)}</p>
      )}

      {loaded && present.length > 0 && (
        <div role="group" aria-label={TODAY.filterLabel} className="today-filters" data-testid="today-filters">
          <button type="button" className="chip-btn" aria-pressed={filter === "all"} onClick={() => setFilter("all")} data-testid="today-filter-all">{TODAY.all} <span className="chip-count">{total}</span></button>
          {present.map((g) => (
            <button key={g} type="button" className="chip-btn" aria-pressed={filter === g} onClick={() => setFilter(g)} data-testid={`today-filter-${g}`}>
              {TODAY.groups[g].short} <span className="chip-count" data-testid={`today-count-${g}`}>{countLabel(g)}</span>
            </button>
          ))}
          <Link className="btn btn-small" to="/coordinator" data-testid="full-worklist">{TODAY.fullWorklist}</Link>
        </div>
      )}
      {(!loaded || present.length === 0) && <p><Link className="btn" to="/coordinator" data-testid="full-worklist">{TODAY.fullWorklist}</Link></p>}

      <div aria-live="polite" className="today-live">
        {flash && <p className="banner banner-ok" role="status" data-testid="today-flash">{flash}</p>}
      </div>
      {error && <p className="banner banner-bad" role="alert" data-testid="today-error">{error}</p>}
      {!loaded && !error && <p role="status">{TODAY.loading}</p>}

      {loaded && total === 0 && !searching && (
        <p className="today-clear" role="status" id="today-empty" tabIndex={-1} data-testid="today-empty"><span aria-hidden="true" className="today-tick">&#10003;</span> {TODAY.empty}</p>
      )}
      {loaded && searching && <p className="muted" data-testid="today-searching">{TODAY.nothingYet}</p>}

      {loaded && total > 0 && (
        <section className="panel today-group" aria-labelledby="today-h-list" data-testid="today-list">
          <h2 id="today-h-list" tabIndex={-1} ref={listHeading}>{TODAY.listTitle}</h2>
          <ul className="plain today-list">
            {visible.flatMap((g) => shown[g].map((item) => isAgencyItem(item)
              ? <AgencyRow key={`${g}:${item.task_id}`} item={item} group={g} />
              : <CaseRow key={`${g}:${item.case_id}`} group={g} item={item} role={role} busy={busy} note={rowNote?.key === `${g}:${item.case_id}` ? rowNote.text : null} onAct={act} />))}
          </ul>
          {(visible.includes("agency") && more.agency) && (
            <p><button type="button" className="btn" disabled={busy === "agency"} onClick={showMoreAgency} data-testid="today-agency-more">{TODAY.showMore}</button></p>
          )}
        </section>
      )}

      {loaded && (
        <p className="muted today-scan" role="status" data-testid="today-scan">
          {complete ? TODAY.allRead(scanned) : TODAY.readSoFar(scanned)}{" "}
          {!complete && (
            <button type="button" className="btn btn-small" disabled={busy === "scan"} onClick={lookFurther} data-testid="today-more">
              {busy === "scan" ? TODAY.lookingFurther : TODAY.lookFurther}
            </button>
          )}
        </p>
      )}
    </main>
  );
}

function CaseRow({ group, item, role, busy, note, onAct }: {
  group: TodayGroupId; item: TodayItem; role: Role; busy: string | null; note: string | null; onAct: (g: TodayGroupId, i: TodayItem, a: TodayActionId) => void;
}) {
  const notes = group === "replies" ? replyNotes(item) : [];
  const actions = item.actions.filter((a) => a !== "open_case");
  // A greyed first button reads as a dead end: when the viewer cannot do the first action, "Open case" is the filled one and the
  // action stays below it with its reason in view.
  const anyAllowed = actions.some((a) => actionState(a, role, item).allowed);
  return (
    <li className="today-item" data-testid="today-item" data-case={item.case_id} data-group={group}>
      <div className="today-head">
        <span className="chip chip-group" data-testid="today-row-group">{TODAY.groups[group].short}</span>
        <Link className="today-case mono" to={`/coordinator/${item.case_id}`} data-testid="today-case-id" aria-label={`Patient ${shortId(item.case_id)}`}>{shortId(item.case_id)}</Link>
        <PathwayBadge pathway={item.pathway} testId="today-pathway" />
        <PriorityBadge priority={item.priority} testId="today-priority" />
        <StatePill state={item.state} />
      </div>
      <p className="today-why" data-testid="today-why">{whyText(item)}</p>
      {item.priority && (
        <p className="today-priority" data-testid="today-priority-reason">
          <strong>Why this priority:</strong> <span data-testid="today-priority-text">{priorityReason(item.priority)}.</span>{" "}
          {priorityIsPlaceholder(item.priority) && <span className="chip chip-warn" data-testid="today-priority-placeholder">{PRIORITY_PLACEHOLDER_NOTE}</span>}
        </p>
      )}
      {notes.length > 0 && <ul className="plain today-notes">{notes.map((n) => <li key={n} className="muted">{n}</li>)}</ul>}
      {group === "signatures" && <p className="muted">{TODAY.signatureNote}</p>}
      <p className="muted today-meta">
        {TODAY.waitingSinceLabel(fmtDateTime(item.since))}
        {item.owner ? ` · Owner: ${item.owner.display_name}` : " · Unowned"}
      </p>
      <div className="today-actions">
        {!anyAllowed && <Link className="btn btn-primary" to={`/coordinator/${item.case_id}`} data-testid="today-open-case">{TODAY.actions.open_case}</Link>}
        {actions.map((a, i) => {
          const st = actionState(a, role, item);
          const id = `${group}:${item.case_id}:${a}`;
          return (
            <ActionButton key={a} primary={i === 0 && st.allowed} testId={`today-${a}`} busy={busy === id || (busy !== null && busy !== "scan" && busy !== "agency")}
              disabledReason={st.allowed ? null : st.why} onClick={() => onAct(group, item, a)}>
              {busy === id ? TODAY.busy[a as keyof typeof TODAY.busy] : TODAY.actions[a as keyof typeof TODAY.actions]}
            </ActionButton>
          );
        })}
        {anyAllowed && <Link className="btn" to={`/coordinator/${item.case_id}`} data-testid="today-open-case">{TODAY.actions.open_case}</Link>}
      </div>
      {note && <p className="banner banner-warn" role="alert" data-testid="today-conflict">{note}</p>}
    </li>
  );
}

function AgencyRow({ item, group }: { item: TodayAgencyItem; group: TodayGroupId }) {
  return (
    <li className="today-item" data-testid="today-item" data-task={item.task_id} data-group={group}>
      <div className="today-head">
        <span className="chip chip-group" data-testid="today-row-group">{TODAY.groups[group].short}</span>
        {item.case_id ? <Link className="today-case mono" to={`/coordinator/${item.case_id}`} data-testid="today-case-id" aria-label={`Patient ${shortId(item.case_id)}`}>{shortId(item.case_id)}</Link> : <span className="muted">No case</span>}
        {item.pathway && <PathwayBadge pathway={item.pathway} testId="today-pathway" />}
        <PriorityBadge priority={item.priority} testId="today-priority" />
        {item.state && <StatePill state={item.state} />}
        <span className="chip chip-auto">{TODAY.agencyBy(item.department)}</span>
      </div>
      <p className="today-why" data-testid="today-why"><strong>{item.title}.</strong> {item.summary}</p>
      <p className="muted today-meta">Suggested: {titleCase(item.recommended_action)} · {TODAY.waitingSinceLabel(fmtDateTime(item.since))}</p>
      {item.case_id && (
        <div className="today-actions">
          <Link className="btn btn-primary" to={`/coordinator/${item.case_id}`} data-testid="today-open-case">{TODAY.actions.open_case}</Link>
        </div>
      )}
    </li>
  );
}
