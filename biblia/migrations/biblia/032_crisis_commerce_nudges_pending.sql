-- DEV-1/2/4/6: кризис-коммерция, nudge второй молитвы, отложенные ответы.

BEGIN;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS owed_donation_ask BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS second_prayer_nudge (
  user_id BIGINT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
  due_at TIMESTAMPTZ NOT NULL,
  topic_snippet TEXT,
  sent_at TIMESTAMPTZ,
  crisis_postpone_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_second_prayer_nudge_due
  ON second_prayer_nudge (due_at)
  WHERE sent_at IS NULL;

CREATE TABLE IF NOT EXISTS pending_reply (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  chat_id BIGINT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INT NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  failed_at TIMESTAMPTZ,
  CONSTRAINT pending_reply_status_chk CHECK (
    status IN ('pending', 'done', 'failed', 'paused')
  )
);

CREATE INDEX IF NOT EXISTS idx_pending_reply_pending
  ON pending_reply (created_at)
  WHERE status = 'pending';

COMMIT;
