-- Leader lease (A31, ADR-041). Several API replicas and several scheduler instances may run at once; exactly one of them
-- holds each named lease and does the periodic work (sweep, outbox drain, automation and evidence scans, agency runs,
-- the FHIR projection drain). The others stay idle (`follower`) and take over when the lease expires.
--
--   name        what is led: 'scheduler' (the scheduler loop's work) or 'api-background' (the API's own loops)
--   holder      the instance that holds it (an opaque string chosen by the instance)
--   expires_at  database time after which anyone may take it; the holder renews it before then
--   token       fencing token: incremented every time the holder CHANGES (not on renewal). A call that carries a token
--               other than the current one is from a deposed leader and is refused (app.leader.is_current).
--
-- Every comparison uses the database's now(), never an instance's clock, so skew between instances cannot make two of
-- them believe they hold the lease.
CREATE TABLE leader_lease (
    name        text        PRIMARY KEY,
    holder      text        NOT NULL,
    expires_at  timestamptz NOT NULL,
    token       bigint      NOT NULL CHECK (token >= 1)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app') THEN
        GRANT SELECT, INSERT, UPDATE ON leader_lease TO closure_app;
    END IF;
END
$$;
