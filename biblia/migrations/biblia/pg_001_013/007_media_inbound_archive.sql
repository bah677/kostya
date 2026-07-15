-- Локальный архив файлов из Telegram перед обработкой (восстановление истории диалогов / админка).

CREATE TABLE IF NOT EXISTS media_inbound_files (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    chat_id         BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    file_unique_id  TEXT,
    file_id_at_capture TEXT NOT NULL,
    media_subtype   TEXT NOT NULL,
    mime_type       TEXT,
    file_size       BIGINT,
    duration_sec    INT,
    sha256_hex      TEXT NOT NULL,
    storage_relpath TEXT NOT NULL,
    messages_row_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_media_inbound_user_created
  ON media_inbound_files (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_inbound_chat_msg
  ON media_inbound_files (chat_id, telegram_message_id);
CREATE INDEX IF NOT EXISTS idx_media_inbound_messages_row
  ON media_inbound_files (messages_row_id)
  WHERE messages_row_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_media_inbound_dedupe
  ON media_inbound_files (user_id, chat_id, telegram_message_id, sha256_hex);
