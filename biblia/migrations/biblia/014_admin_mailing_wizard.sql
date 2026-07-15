-- Мастер /new_mailing: таблица admins + вложения в кампаниях.
-- Идемпотентно: на проде admins могла существовать без created_at / note.

CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id BIGINT PRIMARY KEY
);

ALTER TABLE admins ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE admins ADD COLUMN IF NOT EXISTS note TEXT;

CREATE INDEX IF NOT EXISTS idx_admins_created_at ON admins (created_at DESC);

ALTER TABLE mailing_campaigns
  ADD COLUMN IF NOT EXISTS attachments JSONB DEFAULT NULL;

COMMENT ON COLUMN mailing_campaigns.attachments IS
  'Несколько вложений порядком; когда задано, приоритет над media_type/media_file_id.';
