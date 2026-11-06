"""Judge-experience controls of the hosted demo (ADR-058). Registered only under `auth.demo_controls_active()`.

Four small pieces, none of which touches the state machine:

* **Demo status** (`status`): when the demo was last reset, by whom, and when the next scheduled reset is due. Read from
  `status.json` in the control directory, which the HOST writes after a reset (`deploy/gcp/vm/demo-reset-watch.sh`): the API is
  stopped and its database replaced during a reset, so it cannot be the one to remember it.
* **Reset request** (`request_reset`): the API cannot restore a database under itself (ADR-055), so a supervisor's click writes a
  request file; the host script acts on it. Rate limited by files (so a restart of the API forgets nothing): one accepted request
  per `demo_reset_min_interval_seconds`, and none while one is pending.
* **Fresh cases** (`pick_cases`): the guided path gives each judge session a case nobody has touched since the last reset, in the
  state the step needs, and holds it for that session for `CLAIM_SECONDS`, so two judges walking the same step get different cases.
  "Untouched" is a baseline of event counts taken once per reset (persisted next to the status), compared with the log now.
* **Simulated missed follow-up** (`missed_followup`): moves the simulation clock just past a case's follow-up date and the
  supervisor rung, and runs the real sweep (and the watchdog's idle scan when the quiet stretch is long enough). Everything that
  appears on the case (overdue, the reminder, the supervisor escalation, a watchdog flag) is written by the engine's own paths
  under their own actors; nothing here writes an event.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app import readmodel, watchdog
from app.auth import Identity
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings
from app.routers import cases

log = logging.getLogger("app.demo")

CLAIM_SECONDS = 30 * 60  # a case picked for a judge session is held for it this long
BASELINE_MAX = 5000
SCAN_BUDGET = 1000
PAGE = 200
ADVANCE_MAX_DAYS_PER_CALL = 7  # POST /clock/advance under the demo gate: the most one call may move the shared clock
ADVANCE_WINDOW_HOURS = 6  # ... and the most it may move in total (ADVANCE_MAX_DAYS_PER_WINDOW) in any rolling window this long
ADVANCE_MAX_DAYS_PER_WINDOW = 30
MAX_JUMP_DAYS = 45  # "Simulate a missed follow-up" refuses to move the shared clock further than this


class DemoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ---- the control directory ------------------------------------------------------------------------------------------

def _dir() -> Path:
    return Path(settings.demo_control_dir)


def _read(name: str) -> dict | None:
    try:
        with open(_dir() / name, encoding="utf-8") as fh:
            out = json.load(fh)
        return out if isinstance(out, dict) else None
    except (OSError, ValueError):
        return None


def _write(name: str, body: dict) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{name}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, d / name)  # atomic: the host script never reads half a file


def _wall() -> datetime:
    return datetime.now(UTC)  # wall-clock-ok: reset bookkeeping for the demo status, never read by the engine


def _parse(ts: object) -> datetime | None:
    try:
        out = datetime.fromisoformat(ts) if isinstance(ts, str) and ts else None
        return out.replace(tzinfo=UTC) if out is not None and out.tzinfo is None else out  # a file without an offset means UTC
    except ValueError:
        return None


# ---- status and reset -------------------------------------------------------------------------------------------------

def _text(v: object, limit: int = 200) -> str | None:
    """A string from a host-written file, trimmed; anything else is None. The files are data, never trusted for shape."""
    return v[:limit] if isinstance(v, str) and v else None


def _next_scheduled(st: dict, hours: float, now: datetime) -> str | None:
    """When the timer fires next: its last pass (`last_scheduled_at`, written by the host even when the pass was skipped) plus
    the interval, else the last reset plus the interval (the seed's snapshot counts). A late timer rolls forward to the next
    slot, so the answer is never in the past. None while nothing is known or when scheduled resets are off."""
    anchor = _parse(st.get("last_scheduled_at")) or _parse(st.get("last_reset_at"))
    if anchor is None or hours <= 0:
        return None
    step = timedelta(hours=hours)
    nxt = anchor + step
    if nxt <= now:
        nxt += step * (int((now - nxt) / step) + 1)
    return nxt.isoformat()


def status() -> dict:
    """`{last_reset_at, last_reset_by, last_reset_reason, next_auto_reset_at, reset_pending, reset_id, last_api_restart_at,
    last_api_restart_reason}` (nulls when unknown). Everything here is read from files the HOST writes (the API only writes the
    request file): `status.json` by demo-reset-watch.sh (and once by the seed, `--record-seed`), `api-restart.json` by
    sc-api-restart.sh when its memory gate restarts the API."""
    st = _read("status.json") or {}
    req = _read("reset-request.json")
    rs = _read("api-restart.json") or {}
    nxt = _next_scheduled(st, settings.demo_auto_reset_hours, _wall())
    restarted = _parse(rs.get("at"))
    return {
        "last_reset_at": st.get("last_reset_at"),
        "last_reset_by": st.get("last_reset_by"),
        "last_reset_reason": st.get("last_reset_reason"),
        "next_auto_reset_at": nxt,
        "reset_pending": req is not None,
        "reset_id": st.get("last_reset_at") or "initial",
        "last_api_restart_at": restarted.isoformat() if restarted else None,
        "last_api_restart_reason": _text(rs.get("reason")) if restarted else None,
    }


def request_reset(identity: Identity) -> dict:
    """Write the reset request the host script acts on. Raises DemoError(409) while one is pending and DemoError(429) inside the
    minimum interval. The requester is the display name and role, never an id."""
    if _read("reset-request.json") is not None:
        raise DemoError(409, "A reset is already on its way. The demo is back in about a minute.")
    now = _wall()
    st = _read("status.json") or {}
    last = max(filter(None, (_parse(st.get("last_reset_at")), _parse(st.get("last_request_at")))), default=None)
    wait = settings.demo_reset_min_interval_seconds
    if last is not None and (now - last).total_seconds() < wait:
        left = int(wait - (now - last).total_seconds())
        raise DemoError(429, f"The demo was reset a moment ago. Please wait {max(1, left // 60 + (1 if left % 60 else 0))} minute(s) before resetting again.")
    body = {"requested_at": now.isoformat(), "requested_by": f"{identity.name} ({identity.role})"}
    try:
        _write("reset-request.json", body)
        _write("status.json", {**st, "last_request_at": now.isoformat()})
    except OSError as exc:
        log.error("demo reset request could not be written to %s: %s", settings.demo_control_dir, exc.strerror)
        raise DemoError(503, "Reset is not available on this server right now. Ask the operator to reset it (make gcp-reset).") from exc
    log.info("demo reset requested by role=%s", identity.role)
    return {"requested": True, **body, "message": "Reset requested. The demo restarts in about a minute and comes back at the same sign-in."}


# ---- the shared clock's advance budget ------------------------------------------------------------------------------

def guard_advance(days: float) -> None:
    """Demo gate only: let one POST /clock/advance move the shared clock `days` (days + hours/24), or refuse it. Moving the clock
    is irreversible until the next reset and every judge shares it, so a call is capped (422) and so is the total over a rolling
    window (409). The budget lives in `clock-budget.json` beside the status and is tied to the reset it was taken after, so the
    host's reset (which also deletes the file) and a changed `reset_id` both give the next judge a full budget. Recorded on accept."""
    if days > ADVANCE_MAX_DAYS_PER_CALL:
        raise DemoError(422, f"On the hosted demo the clock moves at most {ADVANCE_MAX_DAYS_PER_CALL} days per click, so every judge keeps a "
                             f"usable demo. Advance by {ADVANCE_MAX_DAYS_PER_CALL} days or fewer.")
    now = _wall()
    rid = status()["reset_id"]
    with _lock:
        saved = _read("clock-budget.json")
        if saved and saved.get("reset_id") == rid and isinstance(saved.get("entries"), list):
            raw = saved["entries"]
        else:
            raw = [[at.isoformat(), d] for at, d in _advances] if not saved and _advances_rid == [rid] else []
        cutoff = now - timedelta(hours=ADVANCE_WINDOW_HOURS)
        entries: list[tuple[datetime, float]] = []
        for item in raw:
            at = _parse(item[0]) if isinstance(item, list) and len(item) == 2 else None
            if at is not None and at > cutoff and isinstance(item[1], (int, float)):
                entries.append((at, float(item[1])))
        used = sum(d for _, d in entries)
        if used + days > ADVANCE_MAX_DAYS_PER_WINDOW + 1e-9:
            free_at = min((at for at, _ in entries), default=now) + timedelta(hours=ADVANCE_WINDOW_HOURS)
            mins = max(1, int((free_at - now).total_seconds() // 60) + 1)
            raise DemoError(409, f"The demo clock has already moved {used:g} of the {ADVANCE_MAX_DAYS_PER_WINDOW} days allowed every "
                                 f"{ADVANCE_WINDOW_HOURS} hours (it is shared by all judges). Try again in about {mins} minute(s), or after the next demo reset.")
        entries.append((now, days))
        _advances[:] = entries
        _advances_rid[:] = [rid]
        try:
            _write("clock-budget.json", {"reset_id": rid, "entries": [[at.isoformat(), d] for at, d in entries]})
        except OSError as exc:
            log.warning("demo clock budget not persisted to %s: %s", settings.demo_control_dir, exc.strerror)


# ---- fresh cases for the guided path --------------------------------------------------------------------------------

_lock = threading.Lock()
_baseline: dict | None = None  # {"reset_id": str, "counts": {case_id: events}} for this process
_claims: dict[str, tuple[str, float]] = {}  # case_id -> (session, expires monotonic)
_picks: dict[tuple[str, str], str] = {}  # (session, step) -> case_id
_advances_rid: list[str] = []  # the reset_id `_advances` belongs to
_advances: list[tuple[datetime, float]] = []  # in-process fallback of the advance budget when the control dir is unwritable


def forget() -> None:
    """Drop in-process memory (tests)."""
    global _baseline, _last_missed
    with _lock:
        _last_missed = 0.0
        _baseline = None
        _claims.clear()
        _picks.clear()
        _advances.clear()
        _advances_rid.clear()


def _mono() -> float:
    import time
    return time.monotonic()  # wall-clock-ok: claim expiry only


def _open_rows(now: datetime) -> list[dict]:
    """A bounded read of the open cases as worklist rows plus the number of events each log holds."""
    calendar = default_calendar()
    rows: list[dict] = []
    for pw in cases.all_pathways():
        after = None
        while len(rows) < SCAN_BUDGET:
            page, nxt = cases.store.load_cases_page(pw, None, after, PAGE, None)
            for lc in page:
                row = readmodel.case_row(lc, pw, calendar)
                if row is None or lc.model is None or lc.model.state == pw.terminal_state or lc.model.erased:
                    continue
                row["_events"] = len(lc.events)
                row["_pathway"] = pw
                rows.append(row)
            if nxt is None:
                break
            after = nxt
    return rows


def _baseline_counts(rows: list[dict], reset_id: str) -> dict[str, int]:
    """The event count of every open case as of the last reset. Taken on the first look after a reset and persisted, so a restart
    of the API does not mistake a judge's work for the seed."""
    global _baseline
    with _lock:
        if _baseline is not None and _baseline["reset_id"] == reset_id:
            return _baseline["counts"]
        saved = _read("baseline.json")
        if saved and saved.get("reset_id") == reset_id and isinstance(saved.get("counts"), dict):
            _baseline = {"reset_id": reset_id, "counts": saved["counts"]}
            return _baseline["counts"]
        counts = {r["case_id"]: r["_events"] for r in rows[:BASELINE_MAX]}
        _baseline = {"reset_id": reset_id, "counts": counts}
    try:
        _write("baseline.json", _baseline)
    except OSError:
        log.warning("demo baseline could not be saved; it holds for this process only")
    return counts


def _has_future_due(r: dict) -> bool:
    return bool(r.get("due_at")) and not r["overdue"] and r["escalation_level"] == 0


# step id -> (predicate over a worklist row, how much it matters that it is exactly this: list of preference scorers)
def _wants(step: str):
    state = lambda *s: (lambda r: r["state"] in s)  # noqa: E731
    pri = lambda r: ((r.get("priority") or {}).get("tier") or "")  # noqa: E731
    if step == "record":
        return lambda r: r["state"] in ("result_received", "acknowledged", "owner_accepted") and r["escalation_level"] == 0
    if step == "prioritise":
        return lambda r: pri(r) in ("urgent", "high") and r["state"] not in ("closed",)
    if step == "refer":
        return lambda r: bool(r.get("insurance")) and r["state"] in ("owner_accepted", "patient_contacted", "patient_acknowledged") and not r.get("pending_handoff")
    if step == "remind":
        return lambda r: r["state"] in ("patient_contacted", "patient_acknowledged", "confirmatory_booked") and r["escalation_level"] == 0
    if step == "outcome":
        return lambda r: r["state"] in ("confirmatory_completed", "clinician_reviewed")
    if step == "safety":
        return lambda r: _has_future_due(r) and r["state"] in ("owner_accepted", "patient_contacted", "patient_acknowledged", "confirmatory_booked", "acknowledged")
    if step == "privacy":
        return lambda r: r["state"] not in ("closed",)
    raise KeyError(step)


STEPS = ("record", "prioritise", "refer", "remind", "outcome", "safety", "privacy")


def pick_cases(session: str) -> dict[str, dict | None]:
    """One fresh case per guided step for this browser session: `{step: {case_id, state, ...} | None}`. A step with no fresh case
    left answers None (the web falls back to the worklist and says so)."""
    now = clock.now()
    rows = _open_rows(now)
    base = _baseline_counts(rows, status()["reset_id"])
    t = _mono()
    out: dict[str, dict | None] = {}
    with _lock:
        for k in [k for k, (_, exp) in _claims.items() if exp < t]:
            del _claims[k]
        for key in [k for k in _picks if _claims.get(_picks[k], (None, 0))[0] != k[0]]:
            del _picks[key]
        mine = {c for c, (s, _) in _claims.items() if s == session}
        taken_by_others = {c for c, (s, _) in _claims.items() if s != session}
        used_here: set[str] = set()
        for step in STEPS:
            row_of = {r["case_id"]: r for r in rows}
            held = _picks.get((session, step))
            chosen = row_of.get(held) if held else None
            if chosen is not None and not _fresh(chosen, base):
                chosen = None  # the judge (or someone else) has acted on it since: give the next fresh one
            if chosen is None:
                want = _wants(step)
                cands = [r for r in rows if want(r) and _fresh(r, base) and r["case_id"] not in taken_by_others
                         and r["case_id"] not in used_here and not (r["case_id"] in mine and (session, step) not in _picks)]
                # Most helpful first: nearest follow-up date, then the case id, so the choice is stable and spread by claims.
                cands.sort(key=lambda r: (r.get("due_at") or "9999", r["case_id"]))
                if not cands and step in ("prioritise", "privacy"):  # these read well on any open case, touched or not
                    cands = [r for r in rows if want(r) and r["case_id"] not in used_here]
                    cands.sort(key=lambda r: r["case_id"])
                chosen = cands[0] if cands else None
            if chosen is not None:
                used_here.add(chosen["case_id"])
                _picks[(session, step)] = chosen["case_id"]
                if _fresh(chosen, base):
                    _claims[chosen["case_id"]] = (session, t + CLAIM_SECONDS)
            out[step] = None if chosen is None else {
                "case_id": chosen["case_id"], "state": chosen["state"], "due_at": chosen.get("due_at"),
                "priority": (chosen.get("priority") or {}).get("tier"), "scheme": (chosen.get("insurance") or {}).get("label"),
                "fresh": _fresh(chosen, base),
            }
    return out


def _fresh(row: dict, base: dict[str, int]) -> bool:
    n = base.get(row["case_id"])
    return n is not None and row["_events"] <= n


# ---- simulate a missed follow-up -----------------------------------------------------------------------------------------

def _case_row(case_id: UUID) -> dict:
    pw = cases.pathway_by_name(cases.store.pathway_name_of(case_id) or "")
    if pw is None:
        raise DemoError(404, "That case was not found. It may have been removed by a reset; go back to Start here.")
    lc = next(iter(cases.store.load_cases_page(pw, None, None, 1, [case_id])[0]), None)
    if lc is None or lc.model is None:
        raise DemoError(404, "That case was not found. It may have been removed by a reset; go back to Start here.")
    row = readmodel.case_row(lc, pw, default_calendar())
    row["_pathway"], row["_events"], row["_lc"] = pw, len(lc.events), lc
    return row


_last_missed = 0.0
MISSED_MIN_INTERVAL_SECONDS = 10  # the clock is shared: one skip per 10 s, so a held-down button cannot run it forward


def missed_followup(case_id: UUID, who: str = "") -> dict:
    """Move the sim clock just past the case's follow-up date and its supervisor rung, running the REAL sweep after each move and
    the watchdog's idle scan at the end. Writes nothing itself."""
    from app.routers.scheduler import _sweep  # the very sweep the scheduler calls

    global _last_missed
    with _lock:
        t = _mono()
        if t - _last_missed < MISSED_MIN_INTERVAL_SECONDS:
            raise DemoError(429, "That was just done. Give it a few seconds, then try again.")
        _last_missed = t

    row = _case_row(case_id)
    pw = row["_pathway"]
    if row["state"] == pw.terminal_state:
        raise DemoError(409, "That case is already closed, so there is no follow-up to miss. Pick an open case.")
    due = _parse(row.get("due_at"))
    if due is None:
        raise DemoError(409, "That case has no follow-up date right now, so nothing can be missed. Pick another case.")
    calendar = default_calendar()
    now = clock.now()
    supervisor_rung = next((r for r in pw.escalation_ladder if r.rung == "supervisor"), None)
    breach_days = supervisor_rung.after_breach_days if supervisor_rung else 3
    first = max(now, due + timedelta(hours=1))
    second = max(first, calendar.add_working_days(due, breach_days) + timedelta(hours=1))
    jump = (second - now).total_seconds() / 86400
    if jump > MAX_JUMP_DAYS:
        raise DemoError(409, f"This case's follow-up is {int(jump)} days away; that is too far to skip in one go. Pick a case that is due sooner.")
    before_events = row["_events"]
    before_level = row["escalation_level"]
    clock_from = clock.now()
    sweeps = []
    for target in (first, second):
        if target > clock.now():
            clock.advance(days=(target - clock.now()).total_seconds() / 86400)
        sweeps.append(_sweep(clock.now()))
    scan = watchdog.run_idle_scan(cases.store, pw, clock.now(), settings.watchdog_idle_days, only=[case_id]).to_json()
    log.info("demo missed follow-up by %s: clock %s to %s", who or "unknown", clock_from.isoformat(), clock.now().isoformat())
    after = _case_row(case_id)
    events = cases.store.events_for(case_id)[before_events:]
    return {
        "case_id": str(case_id),
        "clock_from": clock_from.isoformat(),
        "clock_to": clock.now().isoformat(),
        "days_skipped": round((clock.now() - clock_from).total_seconds() / 86400, 1),
        "new_events": [{"type": e.type, "actor_kind": "person" if not str(e.actor).startswith("system:") else "system"} for e in events],
        "overdue": after["overdue"],
        "escalation_level": after["escalation_level"],
        "escalation_level_before": before_level,
        "watchdog_flagged": after.get("watchdog_flagged") is not None,
        "requested_by": who or None,
        "sweeps": len(sweeps),
        "watchdog_scan": {"flagged": scan.get("flagged", 0)},
    }
