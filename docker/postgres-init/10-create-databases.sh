#!/bin/sh
# Creates the extra databases next to the main one, idempotently:
#   closure_test  the integration tests' own database, so `make test-db` never touches the dev one (ADR-015)
#   hapi          HAPI FHIR's tables, apart from the application's: HAPI keeps the superuser, the API does not,
#                 and the API's role cannot see FHIR data (SEC-1, ADR-029)
# Runs in two places: once on a fresh data volume (docker-entrypoint-initdb.d, local socket, POSTGRES_USER set by the
# image) and on every `docker compose up` through the one-shot `db-init` service (PGHOST/PGPASSWORD set), which also
# covers a volume made before these databases existed. Safe to run any number of times.
set -e
for db in closure_test hapi; do
  exists=$(psql -U "$POSTGRES_USER" -d postgres -At -c "SELECT 1 FROM pg_database WHERE datname = '$db'")
  if [ "$exists" != "1" ]; then
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$db\" OWNER \"$POSTGRES_USER\""
  fi
done
# Every role may connect to a database by default; only the owner needs HAPI's (the API's role has no business there).
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c 'REVOKE CONNECT ON DATABASE hapi FROM PUBLIC'
