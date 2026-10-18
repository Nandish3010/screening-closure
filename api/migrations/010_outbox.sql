-- The send outbox (SEC-2, ADR-030). Approving a draft writes one row here in the SAME transaction as the
-- `message_approved` event and the draft's approval, so an approval that committed can never be lost
-- between "approved" and "sent": a drain (called at the end of the approve request and by the scheduler
-- loop) picks up every pending row and hands it to the notification mock, idempotently, and marks it.
--
-- One row per approved draft (draft_id is the primary key: a draft is approved once, ever). The row holds
-- no message text: the drain reads the text from `drafts`, so an erasure of the draft is an erasure of
-- the message. `approved_at` is simulation time (the `at` of the `message_sent` event the drain writes).
--
-- status: pending  approved, not yet sent or refused; the drain will (re)try it
--         sent     the notification mock delivered it: a `message_sent` event and a `sent` notification exist
--         blocked  the drain refused it, terminally: consent was withdrawn or the channel switched off, the
--                  contact details are flagged invalid, or the case closed. `reason` says which. Nothing went out.
CREATE TABLE outbox (
    seq              bigserial   UNIQUE,
    draft_id         uuid        PRIMARY KEY REFERENCES drafts (draft_id),
    case_id          uuid        NOT NULL REFERENCES cases (id),
    channel          text        NOT NULL CHECK (channel IN ('sms', 'whatsapp', 'phone', 'letter')),
    language         text        NOT NULL CHECK (language IN ('en', 'ar')),
    purpose          text        NOT NULL,
    approved_at      timestamptz NOT NULL,
    status           text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'blocked')),
    reason           text,
    notification_id  uuid,
    attempts         integer     NOT NULL DEFAULT 0,
    last_error       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz,
    CHECK ((status = 'pending') = (completed_at IS NULL)),
    CHECK (status <> 'blocked' OR reason IS NOT NULL)
);

CREATE INDEX outbox_pending_idx ON outbox (seq) WHERE status = 'pending';
CREATE INDEX outbox_case_idx ON outbox (case_id, seq);

-- A completed row is final: only a pending row may change.
CREATE FUNCTION outbox_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'outbox rows are never deleted';
    END IF;
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'outbox row for draft % is already % and cannot change', OLD.draft_id, OLD.status;
    END IF;
    IF (NEW.draft_id, NEW.case_id, NEW.channel, NEW.language, NEW.purpose, NEW.approved_at)
       IS DISTINCT FROM
       (OLD.draft_id, OLD.case_id, OLD.channel, OLD.language, OLD.purpose, OLD.approved_at) THEN
        RAISE EXCEPTION 'only the progress columns of an outbox row may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER outbox_guard_trg
    BEFORE UPDATE OR DELETE ON outbox
    FOR EACH ROW EXECUTE FUNCTION outbox_guard();
