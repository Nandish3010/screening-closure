-- Inbound patient replies and what the reply triage made of them (D13).
--
-- The event log never holds the text of a reply (ADR-019, payloads.py): `reply_received` carries the channel and
-- `text_ref`, which is `reply_id` here, and `reply_classified` carries the intent, the confidence, the model id and
-- whether a person must look. The text lives in this table only, so erasing a patient's replies is deleting rows here
-- (an operator action as the table owner; the application role has no DELETE or UPDATE) and leaves the log intact.
--
-- One row per reply. `reply_id` is the id the caller may supply to retry a request: the same id is the same reply, and
-- the events derive their ids from it, so a retry writes nothing twice. `received_at` is simulation time, like every
-- other `at` in the system. The classification is written in the same INSERT as the text and never changes.
--
-- `needs_human` is true for every reply the model did not handle on its own: low confidence, an intent that always
-- goes to a person (question, other, opt_out), or a model that was not available (`model_id = 'unavailable'`).
CREATE TABLE patient_replies (
    seq            bigserial   UNIQUE,
    reply_id       uuid        PRIMARY KEY,
    case_id        uuid        NOT NULL REFERENCES cases (id),
    channel        text        NOT NULL CHECK (channel IN ('sms', 'whatsapp', 'phone', 'letter')),
    text           text        NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
    language_hint  text        CHECK (language_hint IN ('en', 'ar')),
    intent         text        NOT NULL CHECK (intent IN ('confirm', 'reschedule', 'cannot_attend', 'wrong_number',
                                                          'dont_understand', 'opt_out', 'question', 'already_done', 'other')),
    confidence     real        NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    model_id       text        NOT NULL,
    needs_human    boolean     NOT NULL,
    logged         boolean     NOT NULL,  -- false: the case was closed, so no event could be written; a person must still read it
    received_at    timestamptz NOT NULL,
    received_by    text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX patient_replies_case_idx ON patient_replies (case_id, seq);
CREATE INDEX patient_replies_review_idx ON patient_replies (seq) WHERE needs_human;

-- The application role (009_app_role.sql): add and read replies; no UPDATE, no DELETE.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT ON patient_replies TO closure_app;
        GRANT USAGE, SELECT ON SEQUENCE patient_replies_seq_seq TO closure_app;
    END IF;
END
$$;
