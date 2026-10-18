-- Least-privilege application role (SEC-1 fix 2, ADR-029).
--
-- Until now the API connected as the database owner (a superuser in compose), so anyone who could run SQL
-- through it could also `ALTER TABLE events`, drop the append-only triggers or read every other database. From here
-- the API connects as `closure_app`: it can do exactly what the application does and nothing else.
--
--   * NOT a superuser, no CREATEDB / CREATEROLE / REPLICATION / BYPASSRLS; owns nothing, so it cannot ALTER, DROP,
--     DISABLE TRIGGER or TRUNCATE any table (only an owner can), and cannot create objects in `public`;
--   * the tables stay owned by whoever ran the migrations (the superuser): this script does not change owners;
--   * INSERT and SELECT only on the append-only tables (events, notifications, prompts): even without their triggers
--     the role has no UPDATE, DELETE or TRUNCATE on them;
--   * SELECT, INSERT and UPDATE where the application changes rows (cases, drafts, users, sim_clock, heartbeats,
--     system_alerts), SELECT, INSERT, UPDATE, DELETE only on the projection retry queue, and SELECT on
--     schema_migrations (the runner checks it at start-up);
--   * USAGE on the sequences behind the bigserial columns, nothing else.
--
-- The role is created without a password, so nobody can log in as it until the operator sets one:
-- `python -m app.migrate` does that from APP_DB_PASSWORD (the compose `migrate` service). Migrations themselves
-- (DDL) must run as the owner; the API's own start-up check needs no more than SELECT. A new table in a later
-- migration needs its own GRANT to closure_app, in that migration (010 does so for the dead-letter table).
-- Creating the role needs CREATEROLE (a superuser in compose); the role is cluster-wide, the grants per database.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        CREATE ROLE closure_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;
EXCEPTION
    -- two databases migrating at the same moment (the test suite does) race on the cluster-wide role
    WHEN duplicate_object OR unique_violation THEN NULL;
END
$$;

-- Nobody but the owner creates objects in the schema, whatever the server version's default is.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO closure_app;

-- Append-only tables: the application adds rows and reads them, nothing more.
GRANT SELECT, INSERT ON events, notifications, prompts TO closure_app;

-- Read model and mutable records.
GRANT SELECT, INSERT, UPDATE ON cases, drafts, users, sim_clock, heartbeats, system_alerts TO closure_app;

-- The projection retry queue is worked in place (rows are inserted, rescheduled and removed).
GRANT SELECT, INSERT, UPDATE, DELETE ON fhir_projection_queue TO closure_app;

-- The runner only reads its own bookkeeping.
GRANT SELECT ON schema_migrations TO closure_app;

-- bigserial columns: events.id, notifications.seq, drafts.seq, system_alerts.id
GRANT USAGE, SELECT ON SEQUENCE events_id_seq, notifications_seq_seq, drafts_seq_seq, system_alerts_id_seq TO closure_app;

-- A database that already ran 010 before this role existed: give it the dead-letter list too.
DO $$
BEGIN
    IF to_regclass('public.fhir_projection_dead_letter') IS NOT NULL THEN
        GRANT SELECT, INSERT, DELETE ON fhir_projection_dead_letter TO closure_app;
    END IF;
END
$$;
