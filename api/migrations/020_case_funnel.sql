-- The outcome funnel (ADR-052): `GET /metrics/funnel` reads this table, as `GET /metrics/closure` reads `case_metrics` (ADR-049).
--
-- One row per case of EVERY loaded pathway (case_metrics holds the default pathway only, and its reports are defined on that), with
-- the instants the five steps of the screening loop need: the result recorded, prioritised, referred, attended, outcome documented.
-- DERIVED, never authoritative: `events` stays the only source of truth. `app.metrics_funnel.refresh` fills and keeps it current with
-- the same discipline as the metrics index (changed cases only, keyset batches, a time budget, its own durable cursor per pathway,
-- an advisory lock of its own), so a call never replays more than it can finish.
--
-- The day counts between steps are stored (computed once, with the same arithmetic the replay uses) so the median is a sort and an
-- offset in SQL and equals the replay's `statistics.median` exactly. What depends on `now` (the cases at risk of being lost) is
-- computed from the stored instants at every call.
CREATE TABLE case_funnel (
    case_id            uuid        PRIMARY KEY REFERENCES cases (id) ON DELETE CASCADE,
    src_updated_at     timestamptz NOT NULL,          -- cases.updated_at when the log was read
    sig                text        NOT NULL,          -- what the row depends on besides its log (fold version, pathway)
    pathway            text        NOT NULL,
    ok                 boolean     NOT NULL,          -- false: the log does not replay (the funnel counts and names it)
    error              text,
    state              text,
    t0                 timestamptz,                   -- step 1: the result recorded (case_created)
    priority           text,                          -- step 2: the tier given at intake (NULL: the case was not ranked)
    referred_at        timestamptz,                   -- step 3: first handoff_requested or book_confirmatory
    attended_at        timestamptz,                   -- step 4: first complete_confirmatory
    documented_at      timestamptz,                   -- step 5: first safe close that is not an informed refusal
    declined_at        timestamptz,                   -- first safe close that IS an informed refusal (reported apart)
    d_referred         double precision,              -- t0 -> referred_at, calendar days
    d_attended         double precision,              -- referred_at -> attended_at
    d_documented       double precision,              -- attended_at -> documented_at
    completed          boolean,                       -- the facts the closure metrics use for "lost to follow-up"
    refusal_on_record  boolean,
    lost_flag          boolean,
    surfaced           boolean,
    last_progress_at   timestamptz
);

-- The funnel filters by pathway and tier; a ranked-only or one-pathway call is then a small scan.
CREATE INDEX case_funnel_filter_idx ON case_funnel (pathway, priority) WHERE ok;
-- The list of cases at risk (longest without progress first) walks this in order and stops at its limit.
CREATE INDEX case_funnel_idle_idx ON case_funnel ((coalesce(last_progress_at, t0)), case_id)
    WHERE ok AND NOT completed AND NOT refusal_on_record;
CREATE INDEX case_funnel_failed_idx ON case_funnel (pathway, case_id) WHERE NOT ok;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON case_funnel TO closure_app;
    END IF;
END
$$;
