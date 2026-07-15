-- Идемпотентные «заплатки» после pg_restore легаси Biblia, если не хватает объектов под runtime club_ai.
-- Не трогает сообщения: таблица messages должна соответствовать кодовой базе club_ai
-- (см. bot/logging/message_copier.py и миграции migrations/001–004).
--
-- psql -v ON_ERROR_STOP=1 -h … -U … -d biblia_db_dev -f migrations/biblia/bootstrap_club_runtime_minimal.sql

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS questions_asked INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS interaction_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  event_category TEXT NOT NULL,
  event_type TEXT NOT NULL,
  processing_time_ms INTEGER,
  message_id BIGINT,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  update_id BIGINT,
  chat_id BIGINT,
  chat_type TEXT,
  telegram_message_id BIGINT,
  callback_data TEXT,
  command TEXT,
  source TEXT,
  outcome TEXT
);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_user_created
  ON interaction_logs (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS admin_responses (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  message_text TEXT NOT NULL,
  admin_id BIGINT,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admin_responses_pending
  ON admin_responses (created_at ASC)
  WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS support_tickets (
  ticket_number TEXT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  topic TEXT NOT NULL,
  user_message TEXT NOT NULL,
  admin_response TEXT,
  admin_id BIGINT,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ
);

ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'openai';
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS request_kind TEXT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS raw_usage JSONB;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS request_id TEXT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS thread_id TEXT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS duration_sec INTEGER;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS metadata JSONB;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cached_input_tokens BIGINT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS reasoning_output_tokens BIGINT;

COMMIT;
