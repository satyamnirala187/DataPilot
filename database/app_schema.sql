-- DataPilot: application-owned data for History and Saved Reports, in its own schema, datapilot.
--
--   datapilot.analyses       one row per successful analysis: a snapshot of exactly what the user
--                            saw (question, SQL, result, chart, insight), never rerun
--   datapilot.saved_reports  an analysis the user chose to keep, with a title; it points to the
--                            analysis instead of copying it
--
-- Why a separate schema, not public:
--   - generated SQL can never reach these tables: the SQL validator allows only the six business
--     tables, unqualified or in public, and datapilot_readonly (the role that runs generated SQL)
--     has no access to this schema at all;
--   - Supabase's Data API roles (anon, authenticated) and PUBLIC get nothing here;
--   - schema.sql drops and recreates the business tables when the synthetic data is reseeded; it
--     never touches this schema, so reseeding keeps every analysis and saved report.
--
-- Nothing in this file drops or deletes anything, so it is safe to re-run. IF NOT EXISTS means an
-- existing table is kept as it is: changing a column later needs a migration, not a re-run.
-- Only datapilot_app (app_role.sql) may use these tables, with SELECT and INSERT; rows are never
-- updated or deleted by the running app.
--
-- Run it as the admin role in DATABASE_URL, which owns the schema and the tables (it stops
-- otherwise). database/create_app_role.py runs this file and then app_role.sql.

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_namespace
               WHERE nspname = 'datapilot' AND pg_get_userbyid(nspowner) <> current_user)
       OR EXISTS (SELECT FROM pg_tables
                  WHERE schemaname = 'datapilot' AND tableowner <> current_user) THEN
        RAISE EXCEPTION 'Run this as the owner of schema datapilot and its tables (the admin role).';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS datapilot;


-- One successful analysis, stored exactly as POST /query returned it. account_id is 'demo' for the
-- one shared demo account; ids are UUIDs, so no sequence (and no sequence grant) is needed.
CREATE TABLE IF NOT EXISTS datapilot.analyses (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        TEXT NOT NULL,
    question          TEXT NOT NULL CHECK (char_length(question) BETWEEN 1 AND 500),
    generated_sql     TEXT NOT NULL CHECK (char_length(generated_sql) <= 20000),
    result_columns    JSONB NOT NULL CHECK (jsonb_typeof(result_columns) = 'array'),
    result_rows       JSONB NOT NULL CHECK (jsonb_typeof(result_rows) = 'array'),
    row_count         INTEGER NOT NULL CHECK (row_count >= 0),
    truncated         BOOLEAN NOT NULL,
    visualization     JSONB NOT NULL CHECK (jsonb_typeof(visualization) = 'object'),
    insight           TEXT CHECK (insight IS NULL OR char_length(insight) <= 600),
    snapshot_version  SMALLINT NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- History lists one account's analyses, newest first.
CREATE INDEX IF NOT EXISTS analyses_account_created_idx ON datapilot.analyses (account_id, created_at DESC);


-- An analysis the user saved, at most once. The report's content is its analysis; RESTRICT means an
-- analysis cannot be deleted while a saved report uses it.
CREATE TABLE IF NOT EXISTS datapilot.saved_reports (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id  UUID NOT NULL REFERENCES datapilot.analyses (id) ON DELETE RESTRICT,
    title        TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    saved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (analysis_id)
);


-- Closed to everyone but the owner. A new schema grants nothing by default, but these statements
-- also undo anything granted by hand or by default privileges, on every run. Supabase's API roles
-- (anon, authenticated, service_role) get nothing: DataPilot never uses the Data API. datapilot_readonly
-- is included so the role that runs generated SQL can never be given access here by accident.
REVOKE ALL ON SCHEMA datapilot FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA datapilot FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA datapilot FROM PUBLIC;

DO $$
DECLARE
    other_role text;
BEGIN
    FOREACH other_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'datapilot_readonly'] LOOP
        CONTINUE WHEN NOT EXISTS (SELECT FROM pg_roles WHERE rolname = other_role);  -- not Supabase, or not created yet
        EXECUTE format('REVOKE ALL ON SCHEMA datapilot FROM %I', other_role);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA datapilot FROM %I', other_role);
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA datapilot FROM %I', other_role);
    END LOOP;
END
$$;
