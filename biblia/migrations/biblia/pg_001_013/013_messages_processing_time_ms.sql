-- message_copier.update_message_content пишет processing_time_ms после обработки медиа.
-- Идемпотентно для БД без normalize_to_club_ai/20_messages или старых клонов.

ALTER TABLE messages ADD COLUMN IF NOT EXISTS processing_time_ms INTEGER;

COMMENT ON COLUMN messages.processing_time_ms IS
  'Время обработки контента (медиа, распознавание), мс; см. MessageCopier';
