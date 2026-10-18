-- The agency task ledger (A22, ADR-033): agents are labour, not authority.
--
-- One row per unit of agency work. A run is a tree: the supervisor's root row (department `supervisor`,
-- run_id = id), one dispatch row per department (agent_type `department_head`, parent = the root), and the
-- executioner tasks each head creates (parent = its dispatch row). Nothing here is a case event and nothing here
-- changes a case: the case's state lives in `events` alone. A row records what an agent was asked (`input`: ids,
-- refs and labels only, never patient text), what it produced (`output`), how (`prompt_hash`, `provider`,
-- `model_id`, `fallback_used`) and when, in SIMULATION time (`created_at`, `started_at`, `finished_at`).
--
-- Ids are deterministic (uuid5 of the run and, for an executioner task, the case and the task type), so a repeated
-- run inserts nothing new (ON CONFLICT DO NOTHING in the store).
--
-- Append-only except status transitions: the trigger below refuses DELETE, any change to what a task WAS (id, run,
-- parent, department, agent type, case, input, created_at, created_by), a status move outside
--     queued -> running | failed        running -> done | failed | needs_human        needs_human -> done | failed
-- and overwriting a value once set (started_at, finished_at, output, the provenance columns, the resolution).
-- A finished task must carry its provenance (the CHECK below), so the ledger cannot hold a result without saying
-- which planner or tool produced it.
CREATE TABLE agency_tasks (
    seq            bigserial   UNIQUE,
    id             uuid        PRIMARY KEY,
    run_id         uuid        NOT NULL,
    parent_id      uuid        REFERENCES agency_tasks (id),
    department     text        NOT NULL CHECK (department IN
                       ('supervisor', 'outreach', 'referral_scheduling', 'evidence_closure', 'patient_support',
                        'quality_ops')),
    agent_type     text        NOT NULL CHECK (agent_type ~ '^[a-z][a-z_]{1,48}$'),
    case_id        uuid        REFERENCES cases (id),
    status         text        NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'needs_human')),
    input          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    output         jsonb,
    prompt_hash    text        CHECK (prompt_hash IS NULL OR prompt_hash ~ '^[0-9a-f]{64}$'),
    provider       text,
    model_id       text,
    fallback_used  boolean,
    created_at     timestamptz NOT NULL,
    started_at     timestamptz,
    finished_at    timestamptz,
    created_by     text        NOT NULL CHECK (created_by LIKE 'system:agency:%' OR created_by LIKE 'supervisor:%'),
    resolved_at    timestamptz,
    resolved_by    text,
    resolution     jsonb,
    CHECK ((parent_id IS NULL) = (run_id = id)),
    CHECK (status IN ('queued', 'running')
           OR (finished_at IS NOT NULL AND output IS NOT NULL AND provider IS NOT NULL AND model_id IS NOT NULL
               AND fallback_used IS NOT NULL)),
    CHECK ((resolved_at IS NULL) = (resolution IS NULL))
);

CREATE INDEX agency_tasks_run_idx ON agency_tasks (run_id, seq);
CREATE INDEX agency_tasks_case_idx ON agency_tasks (case_id, seq) WHERE case_id IS NOT NULL;
CREATE INDEX agency_tasks_open_idx ON agency_tasks (status, seq) WHERE status IN ('queued', 'running', 'needs_human');

CREATE FUNCTION agency_tasks_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'agency tasks are never deleted';
    END IF;
    IF (NEW.id, NEW.run_id, NEW.parent_id, NEW.department, NEW.agent_type, NEW.case_id, NEW.input,
        NEW.created_at, NEW.created_by, NEW.seq)
       IS DISTINCT FROM
       (OLD.id, OLD.run_id, OLD.parent_id, OLD.department, OLD.agent_type, OLD.case_id, OLD.input,
        OLD.created_at, OLD.created_by, OLD.seq) THEN
        RAISE EXCEPTION 'agency task %: only its status and progress may change', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'queued' AND NEW.status IN ('running', 'failed'))
        OR (OLD.status = 'running' AND NEW.status IN ('done', 'failed', 'needs_human'))
        OR (OLD.status = 'needs_human' AND NEW.status IN ('done', 'failed'))) THEN
        RAISE EXCEPTION 'agency task %: % -> % is not a permitted transition', OLD.id, OLD.status, NEW.status;
    END IF;
    IF (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at)
       OR (OLD.output IS NOT NULL AND NEW.output IS DISTINCT FROM OLD.output)
       OR (OLD.prompt_hash IS NOT NULL AND NEW.prompt_hash IS DISTINCT FROM OLD.prompt_hash)
       OR (OLD.provider IS NOT NULL AND NEW.provider IS DISTINCT FROM OLD.provider)
       OR (OLD.model_id IS NOT NULL AND NEW.model_id IS DISTINCT FROM OLD.model_id)
       OR (OLD.fallback_used IS NOT NULL AND NEW.fallback_used IS DISTINCT FROM OLD.fallback_used)
       OR (OLD.resolved_at IS NOT NULL AND NEW.resolved_at IS DISTINCT FROM OLD.resolved_at)
       OR (OLD.resolved_by IS NOT NULL AND NEW.resolved_by IS DISTINCT FROM OLD.resolved_by)
       OR (OLD.resolution IS NOT NULL AND NEW.resolution IS DISTINCT FROM OLD.resolution) THEN
        RAISE EXCEPTION 'agency task %: a recorded value cannot be overwritten', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER agency_tasks_guard_trg
    BEFORE UPDATE OR DELETE ON agency_tasks
    FOR EACH ROW EXECUTE FUNCTION agency_tasks_guard();

CREATE TRIGGER agency_tasks_no_truncate
    BEFORE TRUNCATE ON agency_tasks
    FOR EACH STATEMENT EXECUTE FUNCTION agency_tasks_guard();

-- The application role (009_app_role.sql, ADR-029) inserts tasks and moves them on: no DELETE.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT, UPDATE ON agency_tasks TO closure_app;
        GRANT USAGE, SELECT ON SEQUENCE agency_tasks_seq_seq TO closure_app;
    END IF;
END
$$;
