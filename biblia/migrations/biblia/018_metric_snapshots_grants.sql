-- Исправление 018: роль называется biblia_bot_user, не bot_user.

BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'biblia_bot_user') THEN
    EXECUTE 'GRANT ALL ON TABLE metric_snapshots TO biblia_bot_user';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE metric_snapshots_id_seq TO biblia_bot_user';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'biblia_bot_user_dev') THEN
    EXECUTE 'GRANT ALL ON TABLE metric_snapshots TO biblia_bot_user_dev';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE metric_snapshots_id_seq TO biblia_bot_user_dev';
  END IF;
END$$;

COMMIT;
