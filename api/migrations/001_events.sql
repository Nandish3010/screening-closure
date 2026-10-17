-- Initial schema (pre-release: `make reset` wipes the volume, so this is a single file).
--
-- events is the append-only source of truth. cases.current_state / updated_at are a
-- derived read model, re-verified against a replay of events on every append.

CREATE TABLE cases (
    id             uuid        PRIMARY KEY,
    pathway        text        NOT NULL,
    current_state  text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE events (
    id          bigserial   PRIMARY KEY,
    event_id    uuid        NOT NULL UNIQUE,
    case_id     uuid        NOT NULL REFERENCES cases (id),
    seq         integer     NOT NULL,
    type        text        NOT NULL,
    at          timestamptz NOT NULL,
    actor       text        NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (case_id, seq)
);

CREATE INDEX events_case_id_seq_idx ON events (case_id, seq);

-- Enforce append-only at the database level.
CREATE FUNCTION events_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_no_update_delete
    BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_forbid_mutation();

CREATE TRIGGER events_no_truncate
    BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_forbid_mutation();
