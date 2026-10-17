#!/bin/sh
# Runs once, on a fresh data volume only (docker-entrypoint-initdb.d). Creates the separate
# database the integration tests use, so `make test-db` never touches the dev database
# (ADR-015). On an existing volume `make test-db` creates it with the same SQL if missing.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -c "CREATE DATABASE closure_test OWNER \"$POSTGRES_USER\""
