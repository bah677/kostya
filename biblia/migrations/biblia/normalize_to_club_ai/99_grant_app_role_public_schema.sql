-- Один раз от суперпользователя (sudo -u postgres psql), если приложение (.env DB_USER)
-- получает permission denied на таблицы (владелец postgres после pg_restore/template).
--
-- Перед запуском замените в DECLARE имя роли на то же значение, что DB_USER у бота.
--
--   sudo -u postgres psql -d biblia_db_dev -v ON_ERROR_STOP=1 -f migrations/biblia/normalize_to_club_ai/99_grant_app_role_public_schema.sql
--
BEGIN;

DO $body$
DECLARE
  app_role text := 'club_db_user';  -- <-- то же, что DB_USER в .env (например bot_user)
BEGIN
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', app_role);
  EXECUTE format('GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO %I', app_role);
  EXECUTE format('GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO %I', app_role);
  EXECUTE format(
    'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO %I',
    app_role
  );

  -- Новые таблицы/последовательности под владельцем postgres после миграций
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO %I',
    app_role
  );
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO %I',
    app_role
  );
END $body$;

COMMIT;
