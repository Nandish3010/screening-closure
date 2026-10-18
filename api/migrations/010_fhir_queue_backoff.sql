-- FHIR projection retry queue with backoff and a dead-letter list (SEC-1 fix 4).
--
-- Before: the drain read the oldest 100 rows every pass. An event HAPI refuses for good stayed at the
-- head for ever, and 100 of them starved every event behind. Now each row carries `next_attempt_at`
-- (a failure pushes it out exponentially, see app.fhir_projection.RetryPolicy), the drain orders by it,
-- and a row that has used all its attempts moves to `fhir_projection_dead_letter`, which GET /alerts
-- reports as a count. Like the queue itself, neither table holds clinical data, only ids and the last
-- error text. The database clock (now()) schedules the retries: it is about HAPI's health, not a case.

ALTER TABLE fhir_projection_queue
    ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX fhir_projection_queue_due_idx ON fhir_projection_queue (next_attempt_at, event_id);

CREATE TABLE fhir_projection_dead_letter (
    event_id         uuid        PRIMARY KEY,
    case_id          uuid        NOT NULL REFERENCES cases (id),
    attempts         integer     NOT NULL,
    last_error       text        NOT NULL DEFAULT '',
    queued_at        timestamptz NOT NULL,
    dead_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX fhir_projection_dead_letter_case_idx ON fhir_projection_dead_letter (case_id);

-- The application role (009_app_role.sql) works the queue; it gets the same rights on the new table.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON fhir_projection_dead_letter TO closure_app;
    END IF;
END
$$;
