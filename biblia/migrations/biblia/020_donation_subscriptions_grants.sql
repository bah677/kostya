-- Права на donation_subscriptions для роли приложения (DB_USER в .env).
-- 019 мог выдать GRANT только bot_user, тогда как prod использует biblia_bot_user.
-- Выполнить от суперпользователя: sudo -u postgres psql -d biblia_bot -f ...

BEGIN;

DO $body$
DECLARE
  roles text[] := ARRAY[
    'biblia_bot_user',
    'biblia_bot_user_dev',
    'bot_user',
    'club_db_user'
  ];
  r text;
BEGIN
  IF to_regclass('public.donation_subscriptions') IS NULL THEN
    RAISE NOTICE '[020] donation_subscriptions отсутствует — пропуск';
    RETURN;
  END IF;

  FOREACH r IN ARRAY roles LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT ALL ON TABLE donation_subscriptions TO %I', r);
      EXECUTE format(
        'GRANT USAGE, SELECT ON SEQUENCE donation_subscriptions_id_seq TO %I',
        r
      );
      RAISE NOTICE '[020] GRANT donation_subscriptions → %', r;
    END IF;
  END LOOP;
END $body$;

COMMIT;
