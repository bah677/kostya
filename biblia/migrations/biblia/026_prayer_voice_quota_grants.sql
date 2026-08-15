-- 025 создал таблицы от postgres без GRANT приложению → permission denied.
-- Идемпотентно выдаём права роли бота.

BEGIN;

DO $body$
DECLARE
  roles text[] := ARRAY[
    'biblia_bot_user',
    'biblia_bot_user_dev',
    'bot_user'
  ];
  r text;
BEGIN
  IF to_regclass('public.bot_runtime_settings') IS NULL
     OR to_regclass('public.prayer_voice_quota_log') IS NULL THEN
    RAISE NOTICE '[026] prayer voice quota tables missing — skip';
    RETURN;
  END IF;

  FOREACH r IN ARRAY roles LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE bot_runtime_settings TO %I', r
      );
      EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE prayer_voice_quota_log TO %I', r
      );
      EXECUTE format(
        'GRANT USAGE, SELECT ON SEQUENCE prayer_voice_quota_log_id_seq TO %I', r
      );
      RAISE NOTICE '[026] GRANT prayer voice quota → %', r;
    END IF;
  END LOOP;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agency_ro') THEN
    GRANT SELECT ON TABLE bot_runtime_settings TO agency_ro;
    GRANT SELECT ON TABLE prayer_voice_quota_log TO agency_ro;
    GRANT SELECT ON SEQUENCE prayer_voice_quota_log_id_seq TO agency_ro;
  END IF;
END $body$;

-- дефолт суточного лимита: 5
INSERT INTO bot_runtime_settings (key, value, updated_at)
VALUES ('prayer_voice_daily_limit', '5', NOW())
ON CONFLICT (key) DO UPDATE SET
  value = EXCLUDED.value,
  updated_at = NOW();

COMMIT;
