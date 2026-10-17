-- Append-only event log. State is derived by replay, never stored.
CREATE TABLE IF NOT EXISTS events (
    id          bigserial   PRIMARY KEY,
    case_id     uuid        NOT NULL,
    type        text        NOT NULL,
    at          timestamptz NOT NULL,
    actor       text        NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_case_id_idx ON events (case_id, id);

-- Enforce append-only at the database level.
CREATE OR REPLACE FUNCTION events_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_no_update_delete ON events;
CREATE TRIGGER events_no_update_delete
    BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_forbid_mutation();

DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate
    BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_forbid_mutation();
