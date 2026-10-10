-- DataPilot: read-only role used by the API to run user queries.
--
-- The password is NOT stored here. database/create_readonly_role.py runs this file, sets a
-- freshly generated password and writes READONLY_DATABASE_URL to .env.
--
-- Re-run create_readonly_role.py after applying schema.sql: dropping and recreating the
-- tables also removes these grants. On Supabase, also run revoke_api_roles.py once
-- (api_roles.sql) so the Data API roles get no access to the tables.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'datapilot_readonly') THEN
        CREATE ROLE datapilot_readonly LOGIN;
    END IF;
END
$$;

-- No special powers, no inherited privileges from other roles, few connections.
-- (NOSUPERUSER is the default; only a superuser may even state it, so it is not repeated here.)
ALTER ROLE datapilot_readonly
    LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
    CONNECTION LIMIT 10;

-- Start from nothing on the public schema, then grant only what is needed.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM datapilot_readonly;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM datapilot_readonly;
REVOKE ALL ON SCHEMA public FROM datapilot_readonly;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO datapilot_readonly', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO datapilot_readonly;
GRANT SELECT ON customers, categories, products, orders, order_items, payments TO datapilot_readonly;

-- Defense in depth: every session for this role starts read-only with short timeouts.
-- The executor also sets these per transaction.
ALTER ROLE datapilot_readonly SET default_transaction_read_only = on;
ALTER ROLE datapilot_readonly SET statement_timeout = '5s';
ALTER ROLE datapilot_readonly SET idle_in_transaction_session_timeout = '15s';
