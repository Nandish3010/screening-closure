-- Notification mock (A7). The only outbound path in the system, and it never sends anything:
-- a row here is the whole "delivery". Append-only like the event log.
--
-- status: queued           approved, not yet delivered (reserved; the mock delivers at once)
--         sent             logged as sent: the mock delivered it, and a message_sent event exists
--         blocked_consent  refused: consent withdrawn or the channel is switched off
--         blocked_contact  refused: the patient's contact details are flagged invalid
-- event_id is the message_sent event this row belongs to (null for a blocked message: nothing
-- was sent, so there is no message_sent). It is deliberately not a foreign key: the event is
-- always written first, and a foreign key would make `TRUNCATE events` fail on the constraint
-- before the append-only trigger could refuse it. created_at is simulation time, like every other
-- timestamp the API writes.
CREATE TABLE notifications (
    seq         bigserial   PRIMARY KEY,
    id          uuid        NOT NULL UNIQUE,
    case_id     uuid        NOT NULL REFERENCES cases (id),
    channel     text        NOT NULL CHECK (channel IN ('sms', 'whatsapp', 'phone', 'letter')),
    language    text        NOT NULL CHECK (language IN ('en', 'ar')),
    purpose     text        NOT NULL,
    body        text        NOT NULL,
    status      text        NOT NULL CHECK (status IN ('queued', 'sent', 'blocked_consent', 'blocked_contact')),
    reason      text,
    event_id    uuid,
    created_at  timestamptz NOT NULL,
    CHECK ((status = 'sent') = (event_id IS NOT NULL))
);

CREATE INDEX notifications_case_idx ON notifications (case_id, seq);

CREATE FUNCTION notifications_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'notifications is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER notifications_no_update_delete
    BEFORE UPDATE OR DELETE ON notifications
    FOR EACH ROW EXECUTE FUNCTION notifications_forbid_mutation();

CREATE TRIGGER notifications_no_truncate
    BEFORE TRUNCATE ON notifications
    FOR EACH STATEMENT EXECUTE FUNCTION notifications_forbid_mutation();
