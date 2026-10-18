-- Sweep index (A20): the earliest instant at which the scheduler could have anything to do for a case.
--
-- DERIVED, never authoritative. `events` stays the only source of truth; `next_due_at` is computed from the
-- replayed read model by `app.sweep.next_due_at` (the state SLA, each running milestone, the escalation rung that
-- would be next, a pending handoff request) and written in the same transaction as every append
-- (`PostgresEventStore.append_many`), so a committed event and its due time cannot disagree. The sweep reads only
-- the cases with `next_due_at <= now` instead of replaying every open case.
--
-- NULL means "not computed", never "nothing due": the sweep always evaluates such a case and fills the value in
-- (a case created since the last sweep, a row from before this migration, a calendar or pathway change, see
-- `next_due_basis`). A case with nothing left to watch holds 9999-12-31 ("never"), not NULL.
--
-- next_due_basis is a fingerprint of everything the due time depends on besides the log (the working-day calendar and
-- the pathway's SLAs, ladder, milestones and handoff window). A sweep running with a different fingerprint
-- treats every case whose basis differs as "not computed", so a changed holiday list or SLA can never leave a
-- stale due time that hides a breach.
ALTER TABLE cases
    ADD COLUMN next_due_at    timestamptz,
    ADD COLUMN next_due_basis text;

CREATE INDEX cases_next_due_idx ON cases (next_due_at);
