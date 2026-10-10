-- DataPilot: the role the API will use to store History and Saved Reports (schema datapilot).
--
-- It is separate from datapilot_readonly, which runs the generated SQL and stays read-only:
--   datapilot_readonly  SELECT on the six business tables; no access to schema datapilot
--   datapilot_app       SELECT and INSERT on datapilot.analyses and datapilot.saved_reports;
--                       no access to the business tables
-- Never UPDATE, DELETE or TRUNCATE: a stored analysis is a snapshot and is never changed.
--
-- The password is NOT stored here. database/create_app_role.py runs app_schema.sql and then this
-- file, sets a freshly generated password and writes APP_DATABASE_URL to .env. Safe to re-run.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'datapilot_app') THEN
        CREATE ROLE datapilot_app LOGIN;
    END IF;
END
$$;

-- No special powers, no inherited privileges from other roles, few connections.
-- (NOSUPERUSER is the default; only a superuser may even state it, so it is not repeated here.)
ALTER ROLE datapilot_app
    LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
    CONNECTION LIMIT 5;

-- Start from nothing, in both schemas, then grant only what is needed.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM datapilot_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM datapilot_app;
REVOKE ALL ON SCHEMA public FROM datapilot_app;
REVOKE ALL ON ALL TABLES IN SCHEMA datapilot FROM datapilot_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA datapilot FROM datapilot_app;
REVOKE ALL ON SCHEMA datapilot FROM datapilot_app;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO datapilot_app', current_database());
END
$$;
GRANT USAGE ON SCHEMA datapilot TO datapilot_app;
GRANT SELECT, INSERT ON datapilot.analyses, datapilot.saved_reports TO datapilot_app;

-- Every session for this role starts with short timeouts, even outside the app.
ALTER ROLE datapilot_app SET statement_timeout = '5s';
ALTER ROLE datapilot_app SET idle_in_transaction_session_timeout = '15s';
