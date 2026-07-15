-- =========================================================================
-- 002_messages_dedupe.sql
--
-- Удаляет дубликаты из messages, появившиеся из-за того что входящее
-- сообщение пишется И в AccessLoggingMiddleware, И в MessageHandler.
--
-- ВАЖНО:
--   * Для НЕ-callback сообщений: дубль = две и более записи с одинаковыми
--     (chat_id, telegram_message_id). Оставляем самую раннюю по id.
--   * Для callback'ов: одинаковый telegram_message_id — это нормально
--     (одна инлайн-клавиатура, много кликов). Истинным дублем считаем только
--     полностью совпавшую запись с тем же content и тем же временем
--     (округлённым до секунды).
--
-- Стратегия:
--   * Сначала запоминаем "лишние" id'ы во временной таблице.
--   * Покажем сводку.
--   * Удаляем строкой (тем же запросом).
--
-- Транзакция оборачивает всё, можно прервать по Ctrl+C — изменений не будет.
-- Идемпотентно: повторный запуск увидит, что дублей нет, и ничего не удалит.
--
-- БЕЗОПАСНО для уже работающего бота:
--   * только DELETE, никаких ALTER/RENAME.
--   * При параллельной работе старого кода после прогона миграции дубли могут
--     появиться снова — это нормально, чистку повторяем при релизе нового кода.
--
-- Накатывать так:
--   PGPASSWORD=... psql -h <host> -U club_db_user -d club_db \
--     -v ON_ERROR_STOP=1 -f migrations/002_messages_dedupe.sql
-- =========================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Собираем id'ы к удалению во временную таблицу
-- ----------------------------------------------------------------------------
CREATE TEMP TABLE _msg_dups_to_delete (
  id INTEGER PRIMARY KEY,
  reason TEXT NOT NULL
) ON COMMIT DROP;

-- (a) Не-callback: оставляем минимальный id в группе (chat_id, telegram_message_id)
INSERT INTO _msg_dups_to_delete (id, reason)
SELECT m.id, 'non_callback_double_insert'
FROM messages m
JOIN (
  SELECT chat_id, telegram_message_id, MIN(id) AS keep_id
  FROM messages
  WHERE telegram_message_id IS NOT NULL
    AND chat_id IS NOT NULL
    AND message_type <> 'callback'
  GROUP BY chat_id, telegram_message_id
  HAVING COUNT(*) > 1
) g
  ON g.chat_id = m.chat_id
 AND g.telegram_message_id = m.telegram_message_id
 AND m.id <> g.keep_id
WHERE m.message_type <> 'callback';

-- (b) Callback'и: дубль только если совпали (chat_id, telegram_message_id, content, ts_sec)
INSERT INTO _msg_dups_to_delete (id, reason)
SELECT m.id, 'callback_exact_double'
FROM messages m
JOIN (
  SELECT chat_id, telegram_message_id, content,
         date_trunc('second', created_at) AS ts_sec,
         MIN(id) AS keep_id
  FROM messages
  WHERE telegram_message_id IS NOT NULL
    AND chat_id IS NOT NULL
    AND message_type = 'callback'
  GROUP BY chat_id, telegram_message_id, content, date_trunc('second', created_at)
  HAVING COUNT(*) > 1
) g
  ON g.chat_id = m.chat_id
 AND g.telegram_message_id = m.telegram_message_id
 AND g.content = m.content
 AND g.ts_sec = date_trunc('second', m.created_at)
 AND m.id <> g.keep_id
WHERE m.message_type = 'callback';

-- ----------------------------------------------------------------------------
-- 2. Сводка перед удалением
-- ----------------------------------------------------------------------------
DO $$
DECLARE
  v_total INT;
  v_non_cb INT;
  v_cb INT;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE reason='non_callback_double_insert'),
                            count(*) FILTER (WHERE reason='callback_exact_double')
    INTO v_total, v_non_cb, v_cb
    FROM _msg_dups_to_delete;
  RAISE NOTICE '[002] К удалению: всего=% (non_callback=%, callback=%)', v_total, v_non_cb, v_cb;
END $$;

-- ----------------------------------------------------------------------------
-- 3. Удаление
-- ----------------------------------------------------------------------------
DELETE FROM messages
WHERE id IN (SELECT id FROM _msg_dups_to_delete);

COMMIT;

-- Постфактум проверки (выполняются после COMMIT, безопасны):
\echo
\echo '=== ПРОВЕРКА: дублей не должно остаться ==='
SELECT
  'non_callback_dups' AS check,
  COALESCE(SUM(c) - COUNT(*), 0) AS extras_left
FROM (
  SELECT chat_id, telegram_message_id, COUNT(*) AS c
  FROM messages
  WHERE telegram_message_id IS NOT NULL
    AND chat_id IS NOT NULL
    AND message_type <> 'callback'
  GROUP BY chat_id, telegram_message_id
  HAVING COUNT(*) > 1
) t
UNION ALL
SELECT
  'callback_exact_dups',
  COALESCE(SUM(c) - COUNT(*), 0)
FROM (
  SELECT chat_id, telegram_message_id, content,
         date_trunc('second', created_at) AS ts_sec, COUNT(*) AS c
  FROM messages
  WHERE telegram_message_id IS NOT NULL
    AND chat_id IS NOT NULL
    AND message_type = 'callback'
  GROUP BY 1,2,3,4
  HAVING COUNT(*) > 1
) t;

\echo
\echo '=== ИТОГОВЫЙ count(messages) ==='
SELECT count(*) FROM messages;
