-- Run as postgres superuser once.
-- Creates agency DB + RW owner + read-only users for ecosystem DBs (examples).

CREATE USER agency_user WITH PASSWORD 'CHANGE_ME_AGENCY_RW';
CREATE DATABASE agency OWNER agency_user;

-- Read-only role for ecosystem sources (attach to biblia_bot + club_db)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agency_ro') THEN
    CREATE ROLE agency_ro LOGIN PASSWORD 'CHANGE_ME_AGENCY_RO';
  END IF;
END$$;

-- Grant CONNECT on source DBs (adjust names for prod/dev):
-- GRANT CONNECT ON DATABASE biblia_bot TO agency_ro;
-- GRANT CONNECT ON DATABASE club_db TO agency_ro;
-- GRANT CONNECT ON DATABASE biblia_bot_dev TO agency_ro;
-- GRANT CONNECT ON DATABASE club_db_dev TO agency_ro;

-- Then inside each source DB:
-- GRANT USAGE ON SCHEMA public TO agency_ro;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO agency_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agency_ro;
