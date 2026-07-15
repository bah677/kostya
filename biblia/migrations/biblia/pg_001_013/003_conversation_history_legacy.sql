-- =========================================================================
-- 003_conversation_history_legacy.sql
--
-- Переименовывает таблицу conversation_history → conversation_history_legacy.
--
-- ⚠️ ПОРЯДОК НАКАТА НА ПРОД ВАЖЕН:
--   1. Сначала задеплоить НОВЫЙ КОД (он не пишет/не читает conversation_history).
--   2. Затем накатить ЭТУ миграцию.
-- Если накатить ДО деплоя нового кода — старый код упадёт при попытке
-- INSERT в conversation_history.
--
-- На dev-БД безопасно катить сразу — мы уже переключили AgentsClient на
-- get_private_chat_history.
--
-- Идемпотентно: повторный запуск не упадёт.
--
-- Накатывать так:
--   PGPASSWORD=... psql -h <host> -U club_db_user -d club_db \
--     -v ON_ERROR_STOP=1 -f migrations/003_conversation_history_legacy.sql
-- =========================================================================

BEGIN;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'conversation_history'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'conversation_history_legacy'
  ) THEN
    RAISE NOTICE '[003] renaming conversation_history -> conversation_history_legacy';
    EXECUTE 'ALTER TABLE conversation_history RENAME TO conversation_history_legacy';
    -- Переименуем и связанный sequence/index, чтобы по логам было понятно
    EXECUTE 'ALTER SEQUENCE IF EXISTS conversation_history_id_seq '
            'RENAME TO conversation_history_legacy_id_seq';
  ELSE
    RAISE NOTICE '[003] no-op: source table missing or target already exists';
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'conversation_history_legacy'
  ) THEN
    EXECUTE $cmt$
      COMMENT ON TABLE conversation_history_legacy IS
        'Архив исторического контекста агента. Новый код использует messages WHERE chat_type=''private''.'
    $cmt$;
  ELSE
    RAISE NOTICE '[003] COMMENT пропущен: conversation_history_legacy нет';
  END IF;
END $$;

COMMIT;

-- Проверка:
--   SELECT count(*) FROM conversation_history_legacy;
--   \d conversation_history_legacy
