-- Display names live here, not in the event log (A14, ADR-019).
--
-- Events carry `actor = role:user_id` where user_id is an opaque slug (uuid5 of role + name, short
-- form). This table is the only place the name typed at dev-login is stored, so erasing a person
-- is one row here and the immutable log holds nothing to erase. Rows are written at dev-login
-- (and lazily for a valid token whose row is missing, e.g. after a database reset); they are
-- mutable on purpose.
CREATE TABLE users (
    user_id       text        PRIMARY KEY CHECK (user_id ~ '^[A-Za-z0-9_-]{1,64}$'),
    role          text        NOT NULL,
    display_name  text        NOT NULL,
    provider      text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
