-- Права на legacy metric_snapshots (если таблица от postgres).
-- Основной путь теперь: metric_daily_snapshots (создаёт biblia_bot_user сам).
-- Этот файл — на случай, если хотите починить старую таблицу вручную:
--
--   sudo -u postgres psql -d biblia_bot -f migrations/biblia/019_metric_snapshots_grants_fix.sql

BEGIN;

GRANT SELECT, INSERT, UPDATE ON TABLE metric_snapshots TO biblia_bot_user;
GRANT USAGE, SELECT ON SEQUENCE metric_snapshots_id_seq TO biblia_bot_user;

-- RO viewer для agency (если роль есть)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgresqlmironviewer') THEN
    EXECUTE 'GRANT SELECT ON TABLE metric_snapshots TO postgresqlmironviewer';
  END IF;
END$$;

COMMIT;
