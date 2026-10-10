-- DataPilot: remove Supabase's Data API roles' access to the business tables.
--
-- Supabase sets default privileges so that every table and sequence the postgres role creates in
-- public is fully granted to its Data API roles, anon and authenticated (SELECT, INSERT, UPDATE,
-- DELETE, TRUNCATE, ...). DataPilot never uses the Data API, so this file takes those grants
-- away: even if the Data API were enabled, it could not read or change the six tables.
--
-- It also changes the default privileges of the role running it, so tables and sequences that
-- schema.sql recreates later are not granted to anon or authenticated either. Run it as the role
-- that owns the tables and runs schema.sql (the admin user in DATABASE_URL); it stops otherwise.
--
-- Not changed: datapilot_readonly keeps its SELECT grants (readonly_role.sql), the owner keeps
-- full control, and USAGE on the public schema stays, because without object privileges it gives
-- no access to data and Supabase's own tooling expects it. Supabase's defaults for objects created
-- by supabase_admin are not ours to change and do not apply to DataPilot's tables.
--
-- Safe to re-run. database/revoke_api_roles.py runs this file.

DO $$
DECLARE
    api_role text;
BEGIN
    IF EXISTS (SELECT FROM pg_tables
               WHERE schemaname = 'public'
                 AND tablename IN ('customers', 'categories', 'products', 'orders', 'order_items', 'payments')
                 AND tableowner <> current_user) THEN
        RAISE EXCEPTION 'Run this as the owner of the six tables, so the default privileges apply to it.';
    END IF;

    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        CONTINUE WHEN NOT EXISTS (SELECT FROM pg_roles WHERE rolname = api_role);  -- not Supabase

        EXECUTE format('REVOKE ALL ON public.customers, public.categories, public.products, public.orders, '
                       'public.order_items, public.payments FROM %I',
                       api_role);
        -- The identity sequences behind the six tables (public holds nothing else).
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', api_role);

        -- Tables and sequences this role creates in public from now on.
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', api_role);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', api_role);
    END LOOP;
END
$$;
