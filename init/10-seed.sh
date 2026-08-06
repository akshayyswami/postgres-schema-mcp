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

    GRANT CREATE, CONNECT ON DATABASE :"POSTGRES_DB" TO aw_owner;
EOSQL

# install.sql is upstream's, run unmodified. PGOPTIONS applies SET ROLE at connect
# time so every object it creates is owned by aw_owner rather than the superuser,
# without editing the script. \copy is client-side, hence the cd.
cd /seed
PGOPTIONS='-c role=aw_owner' psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --quiet --file install.sql > /dev/null

echo "AdventureWorks seeded."
