-- Draft decisions (D5): a human either approves a draft (approved_at / approved_by, from 007_drafts.sql) or
-- rejects it (rejected_at / rejected_by / rejection_reason, here). One decision per draft, ever.
--
-- A rejection is recorded on the draft row and writes no event: the event vocabulary has no
-- `message_rejected` yet (tracked as A16). Timestamps are simulation time, like every other.

ALTER TABLE drafts
    ADD COLUMN rejected_at       timestamptz,
    ADD COLUMN rejected_by       text,
    ADD COLUMN rejection_reason  text,
    ADD CONSTRAINT drafts_rejection_complete
        CHECK ((rejected_at IS NULL) = (rejected_by IS NULL)
           AND (rejected_at IS NULL) = (rejection_reason IS NULL)),
    ADD CONSTRAINT drafts_rejection_reason_not_blank
        CHECK (rejection_reason IS NULL OR length(btrim(rejection_reason)) > 0),
    ADD CONSTRAINT drafts_one_decision
        CHECK (approved_at IS NULL OR rejected_at IS NULL);

-- Drafts are never deleted; only a still-undecided draft may change, and only by gaining its one
-- decision (approval or rejection). Everything else on the row is frozen from the insert.
CREATE OR REPLACE FUNCTION drafts_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drafts are never deleted';
    END IF;
    IF OLD.approved_at IS NOT NULL THEN
        RAISE EXCEPTION 'draft % is already approved and cannot change', OLD.draft_id;
    END IF;
    IF OLD.rejected_at IS NOT NULL THEN
        RAISE EXCEPTION 'draft % is already rejected and cannot change', OLD.draft_id;
    END IF;
    IF (NEW.draft_id, NEW.case_id, NEW.purpose, NEW.channel, NEW.language, NEW.text, NEW.provider,
        NEW.model_id, NEW.fallback_used, NEW.abstention_reason, NEW.abstained_at, NEW.prompt_hash,
        NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.draft_id, OLD.case_id, OLD.purpose, OLD.channel, OLD.language, OLD.text, OLD.provider,
        OLD.model_id, OLD.fallback_used, OLD.abstention_reason, OLD.abstained_at, OLD.prompt_hash,
        OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'only the approval or rejection columns of a draft may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
