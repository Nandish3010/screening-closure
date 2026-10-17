-- Service heartbeats and system alerts (A11, ADR-023). Neither is a case event: they say whether
-- the scheduler and the watchdog are alive, which is a fact about the running system and not
-- about any patient, and they are measured in wall-clock time (the sim clock stops when nothing
-- advances it, which is exactly when a dead scheduler would go unnoticed).
--
-- heartbeats: one mutable row per service, upserted on every loop. Ages are computed by the
-- database (now() - beat_at), so the API itself never reads the wall clock.
-- system_alerts: a row per episode, opened by the watchdog when a service goes quiet and
-- cleared (cleared_at set) when it resumes. At most one open alert per kind.
CREATE TABLE heartbeats (
    service  text        PRIMARY KEY,
    beat_at  timestamptz NOT NULL DEFAULT now(),
    info     jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE system_alerts (
    id          bigserial   PRIMARY KEY,
    kind        text        NOT NULL,
    since       timestamptz NOT NULL DEFAULT now(),
    cleared_at  timestamptz,
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CHECK (cleared_at IS NULL OR cleared_at >= since)
);

CREATE UNIQUE INDEX system_alerts_one_open_per_kind ON system_alerts (kind) WHERE cleared_at IS NULL;
CREATE INDEX system_alerts_since_idx ON system_alerts (since DESC);
