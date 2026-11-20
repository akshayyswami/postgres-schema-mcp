-- scout_ro gets SELECT. It gets nothing else, and this file is deliberately
-- written as revoke-then-grant so the default is "no privilege" rather than
-- "whatever Postgres happened to hand out".
--
-- This is the layer that actually holds. The tool annotation is a hint the
-- client may ignore; the AST guard is one parser bug away from a bypass. If both
-- of those fail, a write still has to get past the grants below, and there is no
-- INSERT, UPDATE, DELETE, TRUNCATE or CREATE anywhere in them.

\set ON_ERROR_STOP on

-- Nothing is public by default. USAGE is then handed back to scout_ro alone so
-- it can resolve the custom domain types that install.sql creates there; it is
-- pointedly not given CREATE.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO scout_ro;

DO $grants$
DECLARE
    s  text;
    db text := current_database();
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', db);

    -- No temp tables. This is what refuses `CREATE TEMP TABLE t AS SELECT 1;` at
    -- the database layer, independently of the parser rejecting it.
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC, scout_ro', db);

    EXECUTE format('GRANT CONNECT ON DATABASE %I TO scout_ro', db);

    -- USAGE lets the role resolve names in the schema; it confers no rights on
    -- the objects themselves. The five shorthand view schemas (pe/hr/pr/pu/sa)
    -- are included so a human poking at the database by hand is not surprised,
    -- but the retrieval index deliberately skips them (see retrieval.py).
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
$grants$;

-- EXPLAIN needs nothing beyond SELECT, so there is nothing further to grant.

-- Limits on what one connection as scout_ro can cost. A query that materializes a huge
-- set (SELECT * FROM generate_series(1, 1000000000) does) spills to disk until it hits
-- temp_file_limit, and then fails instead of filling the volume.
ALTER ROLE scout_ro CONNECTION LIMIT 8;
ALTER ROLE scout_ro SET temp_file_limit = '256MB';
ALTER ROLE scout_ro SET work_mem = '16MB';

-- A fourth layer, for the functions the AST policy also refuses. PostgreSQL lets PUBLIC
-- execute most of them, so a name the policy missed would still run. Revoking from PUBLIC
-- is the only way to take it from scout_ro. Superusers are unaffected, and so is the seed.
-- Only functions that change state or reach outside the database are listed.
DO $revoke$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE (n.nspname = 'pg_catalog' AND (
                  p.proname IN ('set_config', 'pg_notify', 'nextval', 'setval',
                                'pg_sleep', 'pg_sleep_for', 'pg_sleep_until')
               OR p.proname LIKE 'pg\_%advisory\_%'
               OR p.proname LIKE 'lo\_%'
               OR p.proname IN ('lowrite', 'loread')))
           OR (n.nspname = 'public' AND (
                  p.proname LIKE 'crosstab%'
               OR p.proname IN ('connectby', 'normal_rand')))
    LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
    END LOOP;
END
$revoke$;
