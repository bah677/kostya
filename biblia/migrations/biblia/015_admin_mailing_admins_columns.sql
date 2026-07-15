-- Прод: admins уже была без created_at (014 упал на CREATE INDEX).
-- Идемпотентное дополнение схемы + attachments (если 014 не дошёл до конца).

ALTER TABLE admins ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE admins ADD COLUMN IF NOT EXISTS note TEXT;

CREATE INDEX IF NOT EXISTS idx_admins_created_at ON admins (created_at DESC);

ALTER TABLE mailing_campaigns
  ADD COLUMN IF NOT EXISTS attachments JSONB DEFAULT NULL;

COMMENT ON COLUMN mailing_campaigns.attachments IS
  'Несколько вложений порядком; когда задано, приоритет над media_type/media_file_id.';
