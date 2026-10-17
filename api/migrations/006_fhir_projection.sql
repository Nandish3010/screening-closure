-- Retry queue for the FHIR projection (A12, ADR-024).
--
-- After an event is appended, the API projects it to HAPI. If HAPI is down or refuses, the event
-- log must not be blocked: the event id goes here instead and a background loop retries it. A row
-- exists only while an event is waiting; it is deleted on success. The table holds no clinical
-- data, only ids and the last error text.
CREATE TABLE fhir_projection_queue (
    event_id         uuid        PRIMARY KEY,
    case_id          uuid        NOT NULL REFERENCES cases (id),
    attempts         integer     NOT NULL DEFAULT 1,
    last_error       text        NOT NULL DEFAULT '',
    queued_at        timestamptz NOT NULL DEFAULT now(),
    last_attempt_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX fhir_projection_queue_case_idx ON fhir_projection_queue (case_id);
