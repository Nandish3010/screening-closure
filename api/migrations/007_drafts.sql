-- AI drafts and their provenance (D4, D11).
--
-- prompts holds the exact text a prompt hash covers, so an auditor can reproduce the hash:
--   sha256(system_prompt || E'\n---\n' || prompt_text) = prompt_hash
-- drafts is one row per draft attempt (the model's, or the template's after an abstention).
-- A draft is immutable except for the approval columns, which D5 fills exactly once.

CREATE TABLE prompts (
    prompt_hash    text        PRIMARY KEY CHECK (prompt_hash ~ '^[0-9a-f]{64}$'),
    system_prompt  text        NOT NULL,
    prompt_text    text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE drafts (
    draft_id           uuid        PRIMARY KEY,
    seq                bigserial   NOT NULL,           -- insertion order, for stable listing
    case_id            uuid        NOT NULL REFERENCES cases (id),
    purpose            text        NOT NULL,
    channel            text        NOT NULL,
    language           text        NOT NULL,
    text               text        NOT NULL,
    provider           text        NOT NULL,
    model_id           text        NOT NULL,
    fallback_used      boolean     NOT NULL,
    -- D11: set when the template was used instead of the model (reason code), with sim time.
    abstention_reason  text,
    abstained_at       timestamptz,
    prompt_hash        text        NOT NULL REFERENCES prompts (prompt_hash),
    created_by         text        NOT NULL,
    created_at         timestamptz NOT NULL,           -- simulation time
    approved_at        timestamptz,
    approved_by        text,
    CHECK ((abstention_reason IS NULL) = (abstained_at IS NULL)),
    CHECK (fallback_used OR abstention_reason IS NULL),
    CHECK ((approved_at IS NULL) = (approved_by IS NULL))
);

CREATE INDEX drafts_case_id_idx ON drafts (case_id, created_at, seq);

CREATE FUNCTION prompts_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'prompts is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER prompts_no_update_delete
    BEFORE UPDATE OR DELETE ON prompts
    FOR EACH ROW EXECUTE FUNCTION prompts_forbid_mutation();

-- Drafts are never deleted, and only the approval columns may change, once.
CREATE FUNCTION drafts_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drafts are never deleted';
    END IF;
    IF OLD.approved_at IS NOT NULL THEN
        RAISE EXCEPTION 'draft % is already approved and cannot change', OLD.draft_id;
    END IF;
    IF (NEW.draft_id, NEW.case_id, NEW.purpose, NEW.channel, NEW.language, NEW.text, NEW.provider,
        NEW.model_id, NEW.fallback_used, NEW.abstention_reason, NEW.abstained_at, NEW.prompt_hash,
        NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.draft_id, OLD.case_id, OLD.purpose, OLD.channel, OLD.language, OLD.text, OLD.provider,
        OLD.model_id, OLD.fallback_used, OLD.abstention_reason, OLD.abstained_at, OLD.prompt_hash,
        OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'only approved_at and approved_by of a draft may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER drafts_guard_trg
    BEFORE UPDATE OR DELETE ON drafts
    FOR EACH ROW EXECUTE FUNCTION drafts_guard();
