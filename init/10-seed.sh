#!/usr/bin/env bash
# Create the two roles, then seed AdventureWorks as the owner role.
#
# Layer 3 of the safety model starts here: the server never connects as the role
# that owns these tables. It connects as scout_ro, created below with SELECT and
# nothing else.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    -- Owner. Used only for seeding; the MCP server never authenticates as this.
    CREATE ROLE aw_owner LOGIN PASSWORD 'aw_owner_pw'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

    -- The role the server connects as. No CREATE anywhere, no bypass, no inherit
    -- of anything privileged. Grants are applied in 20-grants.sql, after the
    -- objects exist.
    CREATE ROLE scout_ro LOGIN PASSWORD 'scout_ro_pw'
        NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;

    -- The database name comes from current_database() rather than a psql
    -- variable: the official entrypoint does not export POSTGRES_DB into psql's
    -- variable namespace, and GRANT ... ON DATABASE needs an identifier.
    DO $seed$
    BEGIN
        EXECUTE format('GRANT CREATE, CONNECT ON DATABASE %I TO aw_owner', current_database());
    END
    $seed$;

    -- install.sql puts the custom domains ("Name", "Flag", "AccountNumber", ...)
    -- and the tablefunc extension in public. Postgres 15 dropped the implicit
    -- CREATE grant on public, so the owner needs it back explicitly. scout_ro
    -- gets USAGE only, in 20-grants.sql.
    GRANT CREATE, USAGE ON SCHEMA public TO aw_owner;
EOSQL

# install.sql is upstream's, run unmodified. PGOPTIONS applies SET ROLE at connect
# time so every object it creates is owned by aw_owner rather than the superuser,
# without editing the script. \copy is client-side, hence the cd.
cd /seed
PGOPTIONS='-c role=aw_owner' psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --quiet --file install.sql > /dev/null

echo "AdventureWorks seeded."
