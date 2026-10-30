-- M7-8: the closure and effort metrics read a table, not the log. `GET /metrics/closure` and `GET /metrics/effort` used to replay
-- every case in one call (api/app/metrics.py, api/app/effort.py over `load_cases`): minutes and hundreds of MiB at 1,000,000 cases.
--
-- `case_metrics` holds, per case, the small fixed set of facts those two reports fold out of the log (DERIVED, never
-- authoritative; `events` stays the only source of truth). It is filled and kept current by `app.metrics_index.refresh`, which
-- replays only the cases whose `cases.updated_at` is newer than the row it holds, in keyset batches and inside a time budget,
-- so no call ever replays more than it can finish. The reports are then SQL aggregates over this table: the same formulas, the
-- same numbers (api/tests/test_metrics_index_pg.py compares them with the replay on seeded populations).
--
-- `sig` fingerprints what the stored values depend on (the fold's version, the pathway, the working-day calendar, the assumed
-- minutes): a row with another signature is stale and is recomputed, so a changed holiday list cannot leave old working-day counts.
CREATE TABLE case_metrics (
    case_id              uuid        PRIMARY KEY REFERENCES cases (id) ON DELETE CASCADE,
    src_updated_at       timestamptz NOT NULL,          -- cases.updated_at when the log was read
    sig                  text        NOT NULL,
    created_at           timestamptz NOT NULL,          -- the first event's time: the order key of the reports
    ok                   boolean     NOT NULL,          -- false: the log does not replay (the report counts and names it)
    error                text,
    -- the closure record (app.metrics.CaseRecord), when ok
    t0                   timestamptz,
    state                text,
    first_close_at       timestamptz,
    first_safe_close_at  timestamptz,
    safe_declined        boolean,
    unsafe               boolean,
    completed            boolean,
    last_progress_at     timestamptz,
    lost_flag            boolean,
    surfaced             boolean,
    flagged              boolean,
    declined_closure     boolean,
    refusal_pending      boolean,
    refusal_on_record    boolean,
    ttc_days             double precision,              -- first safe close - t0, calendar days
    ttc_wdays            double precision,              -- ... working days
    breach_providers     text[],                        -- the provider at each mark_overdue (NULL element: none)
    -- the effort accounting (app.effort.case_effort), when ok
    e_human              integer,
    e_auto               integer,
    e_patient            integer,
    e_model              integer,
    e_service            integer,
    e_unpriced           integer,
    e_hmin               double precision,
    e_bmin               double precision,
    e_smin               double precision
);

-- The per-case page of /metrics/effort and the list of replay failures, both in (created_at, id) order.
CREATE INDEX case_metrics_order_idx ON case_metrics (created_at, case_id) WHERE ok;
CREATE INDEX case_metrics_failed_idx ON case_metrics (created_at, case_id) WHERE NOT ok;

-- One row per completed visit of a state (app.metrics.CaseRecord.dwells): the median days per transition is an aggregate over these.
CREATE TABLE case_metric_dwells (
    case_id    uuid             NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    ord        integer          NOT NULL,
    frm        text             NOT NULL,
    to_state   text             NOT NULL,
    days       double precision NOT NULL,
    wdays      double precision NOT NULL,
    PRIMARY KEY (case_id, ord)
);

-- Where the refresh stopped (key 'refresh'): the position in (cases.updated_at, id) order and the signature it was computed for.
CREATE TABLE metrics_index_state (
    key         text        PRIMARY KEY,
    value       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- The refresh walks cases in (updated_at, id) order after its cursor; the agency's changed-set read (cases_changed_page) uses it too.
CREATE INDEX cases_updated_id_idx ON cases (updated_at, id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON case_metrics, case_metric_dwells, metrics_index_state TO closure_app;
    END IF;
END
$$;
