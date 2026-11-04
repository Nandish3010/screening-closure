-- Access audit (privacy centre): who READ a patient's record, and when.
--
-- The event log records what was done to a case, never who only looked at it. A patient is entitled to ask "who looked at my
-- record?", so a staff read of a case (its page, timeline, messages, FHIR ids or stored replies) is written here. A read is
-- not an event: it changes no state, so it stays out of the event log and out of the state machine and the model checker.
--
-- One row per (case, actor, kind) per 15 minutes of simulation time at most (the application writes the row only if none is
-- within the window; app/access_audit.py), so a page left open does not fill the table. `actor` is `role:opaque_id`, as in
-- the event log: the name is resolved from the user directory when the patient reads it, never stored. `at` is simulation
-- time, like every other timestamp. Append-only: the application role has INSERT and SELECT only, and the guard below
-- refuses UPDATE, DELETE and TRUNCATE for everyone else, as for the event log.
CREATE TABLE access_audit (
    seq     bigserial   UNIQUE,
    id      uuid        PRIMARY KEY,
    case_id uuid        NOT NULL REFERENCES cases (id),
    at      timestamptz NOT NULL,
    actor   text        NOT NULL,
    role    text        NOT NULL,
    kind    text        NOT NULL CHECK (kind IN ('case', 'timeline', 'messages', 'fhir', 'replies'))
);

CREATE INDEX access_audit_case_idx ON access_audit (case_id, at DESC);

CREATE FUNCTION access_audit_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'the access audit is append-only: % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER access_audit_no_change BEFORE UPDATE OR DELETE ON access_audit FOR EACH ROW EXECUTE FUNCTION access_audit_guard();
CREATE TRIGGER access_audit_no_truncate BEFORE TRUNCATE ON access_audit FOR EACH STATEMENT EXECUTE FUNCTION access_audit_guard();

-- The application role (009_app_role.sql): add and read rows; no UPDATE, no DELETE.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT ON access_audit TO closure_app;
        GRANT USAGE, SELECT ON SEQUENCE access_audit_seq_seq TO closure_app;
    END IF;
END
$$;
