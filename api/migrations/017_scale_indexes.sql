-- M7-7 scale fixes: an incremental watchdog index, a worklist index, one duplicate index dropped, a durable scan cursor.
--
-- 1. cases.idle_since_at (DERIVED, never authoritative; `events` stays the only source of truth). The watchdog flags an open
--    case no person has acted on for WATCHDOG_IDLE_DAYS (app.watchdog.plan_idle_flag). Whether a case can be flagged at `now`
--    depends only on three facts of its replayed model: the state is not terminal, no flag is set, and `last_human_activity_at`
--    is at least `idle_days` ago. So the column holds `last_human_activity_at` while the case is open and unflagged, and
--    9999-12-31 ("never": closed, or already flagged) otherwise. It is written in the SAME transaction as every append
--    (PostgresEventStore.append_many / append_batches / create_case), like `next_due_at`. The scan then reads only
--    `idle_since_at <= now - idle_days`, an index range, instead of replaying every open case.
--    NULL means "not computed" (a row from before this migration, a bulk load): the scan evaluates such a case and writes
--    the value back, and the full reconciliation pass (below) also re-derives every case, so a wrong or missing value can
--    cost time but cannot hide a case permanently.
ALTER TABLE cases ADD COLUMN idle_since_at timestamptz;

-- Rows that can become due. The "never" rows (closed or already flagged) are left out, so the index stays the size of the
-- cases the watchdog still has to watch.
CREATE INDEX cases_idle_since_idx ON cases (idle_since_at, id) WHERE idle_since_at < '9999-01-01';
-- The not-yet-computed rows, so finding them does not need a table scan.
CREATE INDEX cases_idle_uncomputed_idx ON cases (id) WHERE idle_since_at IS NULL;

-- 2. The worklist (GET /cases?state=...&cursor=...) pages by case id within a state.
CREATE INDEX cases_state_id_idx ON cases (current_state, id);

-- 3. events_case_id_seq_idx duplicates the index behind UNIQUE (case_id, seq) (events_case_id_seq_key): same columns, same
--    size, maintained on every append. The planner already used the duplicate for reads; after the drop it uses the
--    unique one. events_genesis_patient_ref_idx (migration 016) is NOT dropped: app.erasure looks a patient's cases up with
--    `WHERE seq = 1 AND payload ->> 'patient_ref' = ...`, which is exactly that partial expression index.
DROP INDEX IF EXISTS events_case_id_seq_idx;

-- 4. Durable state of the watchdog scan: where the incremental pass and the full reconciliation pass stopped, so a scan
--    that ran out of its time budget, or a restart, resumes instead of starting over. One row, key 'scan'.
CREATE TABLE watchdog_state (
    key         text        PRIMARY KEY,
    value       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT, UPDATE ON watchdog_state TO closure_app;
        -- stale follower heartbeat rows are deleted by the watchdog scan (app.ops.PostgresOpsStore.prune_followers)
        GRANT DELETE ON heartbeats TO closure_app;
    END IF;
END
$$;
