-- =========================================================================
-- 004_messages_unique_inbound.sql
--
-- Ставит частичный уникальный индекс на messages, чтобы будущие повторные
-- INSERT'ы для одного и того же telegram-сообщения физически не проходили
-- (страховка от повторения бага middleware+handler).
--
-- ⚠️ ПОРЯДОК НАКАТА НА ПРОД ВАЖЕН:
--   1. Сначала задеплоить НОВЫЙ КОД (он не делает дублей).
--   2. Прогнать на проде 002_messages_dedupe.sql ещё раз (на случай если
--      между тестом и релизом успели накопиться новые дубли).
--   3. Только после этого накатывать ЭТОТ скрипт.
-- Если накатить, пока ещё работает старый код — INSERT'ы старого кода
-- начнут валиться по unique violation.
--
-- Уникальность накладывается только на не-callback сообщения, потому что
-- у callback-сообщений telegram_message_id одинаков для всех нажатий
-- инлайн-клавиатуры (это нормальное поведение Telegram).
--
-- Идемпотентно: повторный запуск не упадёт.
-- =========================================================================

BEGIN;

-- На всякий случай ещё раз проверим, что дублей в защищаемой выборке нет.
-- Если есть — индекс не создастся, и мы сразу узнаем.
DO $$
DECLARE
  v_extras INT;
BEGIN
  SELECT COALESCE(SUM(c) - COUNT(*), 0)
    INTO v_extras
    FROM (
      SELECT chat_id, telegram_message_id, version, COUNT(*) AS c
      FROM messages
      WHERE telegram_message_id IS NOT NULL
        AND chat_id IS NOT NULL
        AND message_type <> 'callback'
      GROUP BY chat_id, telegram_message_id, version
      HAVING COUNT(*) > 1
    ) t;
  IF v_extras > 0 THEN
    RAISE EXCEPTION '[004] aborting: % duplicate rows still exist; rerun 002 first', v_extras;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS messages_inbound_unique_idx
  ON messages (chat_id, telegram_message_id, COALESCE(version, 1))
  WHERE telegram_message_id IS NOT NULL
    AND message_type <> 'callback';

COMMENT ON INDEX messages_inbound_unique_idx IS
  'Защита от повторного INSERT одного и того же telegram-сообщения '
  '(не-callback). Версии разводятся колонкой version (для edited).';

COMMIT;

-- Проверка:
--   \d+ messages
--   SELECT indexdef FROM pg_indexes WHERE indexname='messages_inbound_unique_idx';
