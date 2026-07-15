-- Таблицы ежедневной рассылки и поля пользователя (идемпотентно для pg_restore Biblia).

BEGIN;

CREATE TABLE IF NOT EXISTS mailing_schedules (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  prompt TEXT NOT NULL,
  openai_model TEXT DEFAULT 'gpt-4o-mini',
  days_of_week TEXT NOT NULL DEFAULT '0,2,4',
  hour INTEGER NOT NULL DEFAULT 12,
  generation_hour INTEGER NOT NULL DEFAULT 8,
  last_generated_text TEXT,
  last_generated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS mailing_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  schedule_id INTEGER REFERENCES mailing_schedules (id) ON DELETE SET NULL,
  message_text TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  telegram_message_id BIGINT,
  error_message TEXT,
  sent_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mailing_logs_user_created
  ON mailing_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_mailing_logs_schedule_status
  ON mailing_logs (schedule_id, status)
  WHERE status = 'pending';

ALTER TABLE users ADD COLUMN IF NOT EXISTS mailing_consent BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_mailing_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mailing_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS donation_button_click INTEGER NOT NULL DEFAULT 0;

COMMIT;
