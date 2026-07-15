-- interaction_logs (вариант A): топ-уровневые столбцы для фильтров и трассировки апдейтов.
-- Применять после миграций 001–004.

ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS update_id BIGINT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS chat_id BIGINT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS chat_type TEXT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS callback_data TEXT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS command TEXT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE interaction_logs ADD COLUMN IF NOT EXISTS outcome TEXT;

CREATE INDEX IF NOT EXISTS idx_interaction_logs_update_id ON interaction_logs (update_id)
  WHERE update_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_interaction_logs_chat_id_created ON interaction_logs (chat_id, created_at DESC)
  WHERE chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_interaction_logs_chat_type_created ON interaction_logs (chat_type, created_at DESC)
  WHERE chat_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_interaction_logs_source_created ON interaction_logs (source, created_at DESC)
  WHERE source IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_interaction_logs_outcome_created ON interaction_logs (outcome, created_at DESC)
  WHERE outcome IS NOT NULL;
