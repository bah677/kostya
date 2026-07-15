-- Приведение messages к схеме club_ai / MessageCopier / get_private_chat_history.
-- Идемпотентные ADD COLUMN и бэкфилл из легаси (в т.ч. старый Biblia: message_text + message_type user|assistant).

BEGIN;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS content TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS role TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS chat_type TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS sender_type TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS chat_id BIGINT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS message_type TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS subtype TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS raw_data JSONB;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata JSONB;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS processing_time_ms INTEGER;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_edited BOOLEAN DEFAULT FALSE;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS version INTEGER;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS thread_id TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS message_id TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS id_ass TEXT;

-- Из старого поля message_text → content (только если колонка есть)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'messages' AND column_name = 'message_text'
  ) THEN
    EXECUTE '
      UPDATE messages
         SET content = message_text
       WHERE content IS NULL AND message_text IS NOT NULL
    ';
  END IF;
END $$;

-- Чистый легаси Biblia: строки assistant / user
UPDATE messages
   SET
     role = CASE lower(trim(message_type::text))
             WHEN 'assistant' THEN 'assistant'
             WHEN 'user' THEN 'user'
             ELSE COALESCE(role, 'user')
           END,
     sender_type = COALESCE(
       sender_type,
       CASE WHEN lower(trim(message_type::text)) = 'assistant' THEN 'bot' ELSE 'user' END
     ),
     message_type = 'text'
 WHERE lower(trim(message_type::text)) IN ('user', 'assistant');

UPDATE messages SET chat_type = COALESCE(chat_type, 'private') WHERE chat_type IS NULL;

UPDATE messages SET role = 'user'
 WHERE role IS NULL OR trim(role) = '';

UPDATE messages SET role = 'assistant', sender_type = COALESCE(sender_type, 'bot')
 WHERE COALESCE(trim(sender_type), '') = 'bot'
   AND (role IS DISTINCT FROM 'assistant');

CREATE INDEX IF NOT EXISTS idx_messages_user_created
  ON messages (user_id, created_at DESC);

COMMIT;
