-- =========================================================================
-- 001_messages_chat_type.sql
--
-- Добавляет колонку messages.chat_type ('private' | 'group' | 'supergroup'
-- | 'channel') и заполняет её по уже сохранённым данным.
--
-- БЕЗОПАСНО для уже работающего бота:
--   * колонка nullable, без NOT NULL
--   * старый код не знает о ней — INSERT'ы его не сломаются
--   * никаких удалений/переименований данных
--
-- Идемпотентно: повторный запуск не упадёт.
--
-- Накатывать так:
--   PGPASSWORD=... psql -h <host> -U club_db_user -d club_db \
--     -v ON_ERROR_STOP=1 -f migrations/001_messages_chat_type.sql
-- =========================================================================

BEGIN;

-- 1. Колонка
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS chat_type TEXT;

COMMENT ON COLUMN messages.chat_type IS
  'Тип чата TG: private | group | supergroup | channel. Заполняется при INSERT.';

-- 2. Бэкфилл из raw_data, если там лежит chat.type
UPDATE messages
SET chat_type = raw_data->'chat'->>'type'
WHERE chat_type IS NULL
  AND raw_data IS NOT NULL
  AND raw_data->'chat'->>'type' IN ('private','group','supergroup','channel');

-- 3. Бэкфилл по знаку chat_id для остальных
--    (callback'и из старого периода: raw_data->chat не выставлен)
UPDATE messages
SET chat_type = CASE
  WHEN chat_id IS NULL THEN NULL
  WHEN chat_id > 0 THEN 'private'
  ELSE 'supergroup'  -- безопасный дефолт для отрицательных id; точный тип при INSERT'е будет писать сам код
END
WHERE chat_type IS NULL
  AND chat_id IS NOT NULL;

-- 4. Индексы под типичные выборки
--    DM конкретного юзера в хронологии — главный потребитель (агент)
CREATE INDEX IF NOT EXISTS idx_messages_user_private_chrono
  ON messages (user_id, created_at DESC)
  WHERE chat_type = 'private';

--    Сообщения юзера в группах для аналитики
CREATE INDEX IF NOT EXISTS idx_messages_user_groups_chrono
  ON messages (user_id, created_at DESC)
  WHERE chat_type IN ('group','supergroup','channel');

--    Сводный по chat_type для быстрых COUNT'ов / выборок типа группы
CREATE INDEX IF NOT EXISTS idx_messages_chat_type_created_at
  ON messages (chat_type, created_at DESC);

COMMIT;

-- Проверка после применения:
--   SELECT chat_type, count(*) FROM messages GROUP BY chat_type ORDER BY 2 DESC;
