-- Erasure of a patient's personal data (A32, ADR-025) while the append-only audit trail survives.
--
-- What this adds:
--   * `erasures`      the erasure register: one row per request, opaque ids only (no patient reference, no name). Who asked,
--                     who approved (a different supervisor), why (a code from a fixed taxonomy), which cases, what was erased.
--   * `erasure_map`   the TRANSIENT map from a patient reference to the opaque token that replaces it in the log. A row lives from
--                     the request until the erasure completes and is then DELETED, so nothing is left that links a case to a
--                     patient. (It is the only place besides FHIR where a pending erasure holds the reference.)
--   * `closure_erasure` a NOLOGIN role that owns ONE narrowly scoped SECURITY DEFINER function, `erasure_scrub_case`. The
--                     append-only guards below let an UPDATE through only when `current_user` is that role, i.e. only inside that
--                     function; the role's column-level UPDATE grants name exactly the erasable columns. Everything else about
--                     the guards is unchanged: an ordinary UPDATE (by the application role, by the owner, by anyone) is still
--                     refused, DELETE and TRUNCATE are still refused, and `closure_app` still has no UPDATE on the append-only
--                     tables. tests/test_erasure.py proves each of those.
--
-- What `erasure_scrub_case(case, old_ref, token, tombstone)` does, for ONE case and nothing else:
--   events.payload            the genesis `patient_ref` (and any other occurrence of the reference) becomes the opaque token; a
--                             free-text `note` becomes the tombstone
--   drafts.text, .rejection_reason   tombstone (the draft id and the prompt hash stay)
--   prompts.prompt_text       tombstone, for a prompt used by no draft of any other case (the hash stays; it can no longer be
--                             re-derived, which is the point)
--   notifications.body        tombstone (status, channel, purpose, ids stay)
--   patient_replies.text      tombstone (intent, confidence, ids stay)
--   agency_tasks, outbox      the reference, wherever it appears in input/output/resolution/last_error, becomes the token
-- It returns the row counts per category as jsonb. It refuses a reference that does not belong to the case.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_erasure') THEN
        CREATE ROLE closure_erasure NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
EXCEPTION
    -- two databases migrating at the same moment (the test suite does) race on the cluster-wide role
    WHEN duplicate_object OR unique_violation THEN NULL;
END
$$;

-- ---- the register and the transient map ----------------------------------------------------------------------------------

CREATE TABLE erasures (
    seq           bigserial   UNIQUE,
    erasure_id    uuid        PRIMARY KEY,
    reason        text        NOT NULL CHECK (reason IN ('data_subject_request', 'consent_withdrawn', 'collected_in_error',
                                                          'regulator_or_court_order', 'retention_period_ended')),
    status        text        NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'completed')),
    requested_by  text        NOT NULL,
    requested_at  timestamptz NOT NULL,                  -- simulation time, like every other timestamp
    approved_by   text,
    approved_at   timestamptz,
    completed_at  timestamptz,
    case_ids      uuid[]      NOT NULL DEFAULT '{}',
    categories    text[]      NOT NULL DEFAULT '{}',
    counts        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CHECK (approved_by IS NULL OR approved_by <> requested_by),                 -- two people
    CHECK ((status = 'requested') = (completed_at IS NULL)),
    CHECK (status = 'requested' OR approved_by IS NOT NULL)
);

-- The register is history: rows are never deleted, and a completed row never changes.
CREATE FUNCTION erasures_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'the erasure register is never deleted';
    END IF;
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'erasure % is completed and cannot change', OLD.erasure_id;
    END IF;
    IF (NEW.erasure_id, NEW.reason, NEW.requested_by, NEW.requested_at, NEW.seq)
       IS DISTINCT FROM (OLD.erasure_id, OLD.reason, OLD.requested_by, OLD.requested_at, OLD.seq) THEN
        RAISE EXCEPTION 'only the approval and completion columns of an erasure may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER erasures_guard_trg BEFORE UPDATE OR DELETE ON erasures FOR EACH ROW EXECUTE FUNCTION erasures_guard();
CREATE TRIGGER erasures_no_truncate BEFORE TRUNCATE ON erasures FOR EACH STATEMENT EXECUTE FUNCTION erasures_guard();

-- Transient: one row per case of the patient (or one row with no case, for a patient who has none) from the request to the
-- completion. Deleted when the erasure completes. Holds the only copy of the reference outside HAPI.
CREATE TABLE erasure_map (
    id           bigserial   PRIMARY KEY,
    erasure_id   uuid        NOT NULL REFERENCES erasures (erasure_id),
    case_id      uuid        REFERENCES cases (id),
    patient_ref  text        NOT NULL CHECK (patient_ref ~ '^Patient/[A-Za-z0-9-]{1,64}$'),
    token        text        CHECK (token ~ '^Patient/erased-[0-9a-f]{32}$')
);

CREATE INDEX erasure_map_erasure_idx ON erasure_map (erasure_id);
-- One open request per patient reference at a time.
CREATE UNIQUE INDEX erasure_map_one_open_per_patient ON erasure_map (patient_ref, coalesce(case_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- Finding a patient's cases is a lookup on the genesis event.
CREATE INDEX events_genesis_patient_ref_idx ON events ((payload ->> 'patient_ref')) WHERE seq = 1;

-- ---- the guards: an UPDATE is allowed through only for the erasure role ---------------------------------------------------

CREATE OR REPLACE FUNCTION events_forbid_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND current_user = 'closure_erasure' THEN
        RETURN NEW;  -- erasure_scrub_case only (A32, ADR-025); the role's grant is UPDATE (payload) and nothing else
    END IF;
    RAISE EXCEPTION 'events is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION notifications_forbid_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND current_user = 'closure_erasure' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'notifications is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION prompts_forbid_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND current_user = 'closure_erasure' THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'prompts is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

-- drafts_guard as 008 left it, plus the erasure exit at the top.
CREATE OR REPLACE FUNCTION drafts_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND current_user = 'closure_erasure' THEN
        RETURN NEW;
    END IF;
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

CREATE OR REPLACE FUNCTION outbox_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND current_user = 'closure_erasure' THEN
        RETURN NEW;
    END IF;
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

CREATE OR REPLACE FUNCTION agency_tasks_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND current_user = 'closure_erasure' THEN
        RETURN NEW;
    END IF;
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'agency tasks are never deleted';
    END IF;
    IF (NEW.id, NEW.run_id, NEW.parent_id, NEW.department, NEW.agent_type, NEW.case_id, NEW.input,
        NEW.created_at, NEW.created_by, NEW.seq)
       IS DISTINCT FROM
       (OLD.id, OLD.run_id, OLD.parent_id, OLD.department, OLD.agent_type, OLD.case_id, OLD.input,
        OLD.created_at, OLD.created_by, OLD.seq) THEN
        RAISE EXCEPTION 'agency task %: only its status and progress may change', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'queued' AND NEW.status IN ('running', 'failed'))
        OR (OLD.status = 'running' AND NEW.status IN ('done', 'failed', 'needs_human'))
        OR (OLD.status = 'needs_human' AND NEW.status IN ('done', 'failed'))) THEN
        RAISE EXCEPTION 'agency task %: % -> % is not a permitted transition', OLD.id, OLD.status, NEW.status;
    END IF;
    IF (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at)
       OR (OLD.output IS NOT NULL AND NEW.output IS DISTINCT FROM OLD.output)
       OR (OLD.prompt_hash IS NOT NULL AND NEW.prompt_hash IS DISTINCT FROM OLD.prompt_hash)
       OR (OLD.provider IS NOT NULL AND NEW.provider IS DISTINCT FROM OLD.provider)
       OR (OLD.model_id IS NOT NULL AND NEW.model_id IS DISTINCT FROM OLD.model_id)
       OR (OLD.fallback_used IS NOT NULL AND NEW.fallback_used IS DISTINCT FROM OLD.fallback_used)
       OR (OLD.resolved_at IS NOT NULL AND NEW.resolved_at IS DISTINCT FROM OLD.resolved_at)
       OR (OLD.resolved_by IS NOT NULL AND NEW.resolved_by IS DISTINCT FROM OLD.resolved_by)
       OR (OLD.resolution IS NOT NULL AND NEW.resolution IS DISTINCT FROM OLD.resolution) THEN
        RAISE EXCEPTION 'agency task %: a recorded value cannot be overwritten', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---- the one function ------------------------------------------------------------------------------------------------------

CREATE FUNCTION erasure_scrub_case(p_case uuid, p_old_ref text, p_token text, p_tombstone text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    genesis_ref text;
    n_events int; n_notes int; n_drafts int; n_prompts int; n_prompts_kept int; n_notifications int; n_replies int;
    n_agency int; n_outbox int;
BEGIN
    IF p_old_ref IS NULL OR p_old_ref !~ '^Patient/[A-Za-z0-9-]{1,64}$' THEN
        RAISE EXCEPTION 'erasure_scrub_case: not a patient reference';
    END IF;
    IF p_token IS NULL OR p_token !~ '^Patient/erased-[0-9a-f]{32}$' THEN
        RAISE EXCEPTION 'erasure_scrub_case: not an erasure token';
    END IF;
    IF p_tombstone IS NULL OR p_tombstone !~ '^\[erased [0-9a-f-]{36}\]$' THEN
        RAISE EXCEPTION 'erasure_scrub_case: not a tombstone';
    END IF;
    SELECT payload ->> 'patient_ref' INTO genesis_ref FROM events WHERE case_id = p_case AND seq = 1;
    IF genesis_ref IS DISTINCT FROM p_old_ref AND genesis_ref IS DISTINCT FROM p_token THEN
        RAISE EXCEPTION 'erasure_scrub_case: case % does not belong to this patient reference', p_case;
    END IF;

    -- the log: the reference becomes the token everywhere it occurs; free-text notes are tombstoned
    UPDATE events SET payload = replace(payload::text, p_old_ref, p_token)::jsonb
     WHERE case_id = p_case AND position(p_old_ref IN payload::text) > 0;
    GET DIAGNOSTICS n_events = ROW_COUNT;
    UPDATE events SET payload = jsonb_set(payload, '{note}', to_jsonb(p_tombstone))
     WHERE case_id = p_case AND jsonb_typeof(payload -> 'note') = 'string' AND payload ->> 'note' <> p_tombstone;
    GET DIAGNOSTICS n_notes = ROW_COUNT;

    UPDATE prompts SET prompt_text = p_tombstone
     WHERE prompt_text <> p_tombstone
       AND prompt_hash IN (SELECT prompt_hash FROM drafts WHERE case_id = p_case)
       AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.prompt_hash = prompts.prompt_hash AND d.case_id <> p_case);
    GET DIAGNOSTICS n_prompts = ROW_COUNT;
    SELECT count(*) INTO n_prompts_kept FROM prompts p
     WHERE p.prompt_text <> p_tombstone
       AND p.prompt_hash IN (SELECT prompt_hash FROM drafts WHERE case_id = p_case);

    UPDATE drafts SET text = p_tombstone, rejection_reason = CASE WHEN rejection_reason IS NULL THEN NULL ELSE p_tombstone END
     WHERE case_id = p_case AND (text <> p_tombstone OR (rejection_reason IS NOT NULL AND rejection_reason <> p_tombstone));
    GET DIAGNOSTICS n_drafts = ROW_COUNT;

    UPDATE notifications SET body = p_tombstone WHERE case_id = p_case AND body <> p_tombstone;
    GET DIAGNOSTICS n_notifications = ROW_COUNT;

    UPDATE patient_replies SET text = p_tombstone WHERE case_id = p_case AND text <> p_tombstone;
    GET DIAGNOSTICS n_replies = ROW_COUNT;

    UPDATE agency_tasks SET input = replace(input::text, p_old_ref, p_token)::jsonb,
                            output = replace(output::text, p_old_ref, p_token)::jsonb,
                            resolution = replace(resolution::text, p_old_ref, p_token)::jsonb
     WHERE case_id = p_case
       AND (position(p_old_ref IN input::text) > 0 OR position(p_old_ref IN coalesce(output::text, '')) > 0
            OR position(p_old_ref IN coalesce(resolution::text, '')) > 0);
    GET DIAGNOSTICS n_agency = ROW_COUNT;

    UPDATE outbox SET last_error = replace(last_error, p_old_ref, p_token)
     WHERE case_id = p_case AND last_error IS NOT NULL AND position(p_old_ref IN last_error) > 0;
    GET DIAGNOSTICS n_outbox = ROW_COUNT;

    RETURN jsonb_build_object('events_reference', n_events, 'event_notes', n_notes, 'prompt_text', n_prompts,
                              'prompt_text_kept_shared', n_prompts_kept, 'draft_text', n_drafts,
                              'notification_body', n_notifications, 'reply_text', n_replies, 'agency_ledger', n_agency,
                              'outbox_error', n_outbox);
END;
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_erasure') THEN
        -- A function can be owned only by a role that may create in the schema (CREATE was revoked from PUBLIC by 009):
        -- lend that for the one ALTER and take it back.
        GRANT CREATE ON SCHEMA public TO closure_erasure;
        GRANT USAGE ON SCHEMA public TO closure_erasure;
        ALTER FUNCTION erasure_scrub_case(uuid, text, text, text) OWNER TO closure_erasure;
        REVOKE CREATE ON SCHEMA public FROM closure_erasure;

        -- exactly the columns the function overwrites, and the SELECT it needs to find them
        GRANT SELECT ON events, drafts, prompts, notifications, patient_replies, agency_tasks, outbox TO closure_erasure;
        GRANT UPDATE (payload) ON events TO closure_erasure;
        GRANT UPDATE (text, rejection_reason) ON drafts TO closure_erasure;
        GRANT UPDATE (prompt_text) ON prompts TO closure_erasure;
        GRANT UPDATE (body) ON notifications TO closure_erasure;
        GRANT UPDATE (text) ON patient_replies TO closure_erasure;
        GRANT UPDATE (input, output, resolution) ON agency_tasks TO closure_erasure;
        GRANT UPDATE (last_error) ON outbox TO closure_erasure;
    END IF;
END
$$;

REVOKE ALL ON FUNCTION erasure_scrub_case(uuid, text, text, text) FROM PUBLIC;

-- The application role (009_app_role.sql, ADR-029): may run the function, keep the register and work the transient map.
-- It still has no UPDATE on events, notifications or prompts.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT EXECUTE ON FUNCTION erasure_scrub_case(uuid, text, text, text) TO closure_app;
        GRANT SELECT, INSERT, UPDATE ON erasures TO closure_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON erasure_map TO closure_app;
        GRANT USAGE, SELECT ON SEQUENCE erasures_seq_seq, erasure_map_id_seq TO closure_app;
    END IF;
END
$$;
