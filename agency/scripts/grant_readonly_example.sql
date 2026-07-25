-- Example grants — run inside each source DB as owner/superuser.
-- Replace agency_ro if you chose another role name.

GRANT CONNECT ON DATABASE current_database() TO agency_ro;  -- may need to run from postgres db instead
GRANT USAGE ON SCHEMA public TO agency_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agency_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO agency_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agency_ro;
