-- scout_ro gets SELECT. It gets nothing else, and this file is deliberately
-- written as revoke-then-grant so the default is "no privilege" rather than
-- "whatever Postgres happened to hand out".
--
-- This is the layer that actually holds. The tool annotation is a hint the
-- client may ignore; the AST guard is one parser bug away from a bypass. If both
-- of those fail, a write still has to get past the grants below, and there is no
-- INSERT, UPDATE, DELETE, TRUNCATE or CREATE anywhere in them.

\set ON_ERROR_STOP on

-- Nothing is public by default.
REVOKE ALL ON DATABASE :"POSTGRES_DB" FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- No temp tables. This is what refuses `CREATE TEMP TABLE t AS SELECT 1;` at the
-- database layer, independently of the parser rejecting it.
REVOKE TEMPORARY ON DATABASE :"POSTGRES_DB" FROM PUBLIC, scout_ro;

GRANT CONNECT ON DATABASE :"POSTGRES_DB" TO scout_ro;

-- USAGE lets the role resolve names in the schema; it confers no rights on the
-- objects themselves. The five shorthand view schemas (pe/hr/pr/pu/sa) are
-- included so a human poking at the database by hand is not surprised, but the
-- retrieval index deliberately skips them (see retrieval.py).
DO $$
DECLARE
    s text;
BEGIN
    FOREACH s IN ARRAY ARRAY[
        'person', 'humanresources', 'production', 'purchasing', 'sales',
        'pe', 'hr', 'pr', 'pu', 'sa'
    ]
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO scout_ro', s);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO scout_ro', s);
        EXECUTE format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA %I TO scout_ro', s);
        -- Anything aw_owner creates later is SELECT-only too, so a future
        -- migration cannot quietly hand this role a writable table.
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE aw_owner IN SCHEMA %I GRANT SELECT ON TABLES TO scout_ro', s
        );
    END LOOP;
END
$$;

-- EXPLAIN needs nothing beyond SELECT, so there is nothing further to grant.
