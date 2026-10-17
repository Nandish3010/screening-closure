-- Persisted simulation clock (A3). The API reads time only from this clock, never from the
-- wall clock, so an API restart must not rewind the demo. One row per named clock; the API
-- uses clock_id 'main' (tests use their own ids and delete them).
CREATE TABLE sim_clock (
    clock_id  text        PRIMARY KEY,
    start_at  timestamptz NOT NULL,
    now_at    timestamptz NOT NULL,
    CHECK (now_at >= start_at)
);
