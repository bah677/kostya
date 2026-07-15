-- Челлендж чтения Писания: план, ежедневные порции, диалог в рамках челленджа.

BEGIN;

CREATE TABLE IF NOT EXISTS scripture_challenges (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  status TEXT NOT NULL DEFAULT 'intake',
  user_request_summary TEXT,
  intake_transcript JSONB NOT NULL DEFAULT '[]'::jsonb,
  duration_days INTEGER,
  delivery_hour SMALLINT NOT NULL DEFAULT 9,
  delivery_minute SMALLINT NOT NULL DEFAULT 0,
  delivery_tz TEXT NOT NULL DEFAULT 'Europe/Moscow',
  current_day INTEGER NOT NULL DEFAULT 0,
  plan_version INTEGER NOT NULL DEFAULT 1,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  last_daily_sent_at TIMESTAMPTZ,
  last_weekly_review_at TIMESTAMPTZ,
  next_delivery_at TIMESTAMPTZ,
  next_weekly_review_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scripture_challenges_user
  ON scripture_challenges (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_scripture_challenges_active_delivery
  ON scripture_challenges (status, next_delivery_at)
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS scripture_challenge_plan_items (
  id BIGSERIAL PRIMARY KEY,
  challenge_id BIGINT NOT NULL REFERENCES scripture_challenges(id) ON DELETE CASCADE,
  day_number INTEGER NOT NULL,
  sort_order INTEGER NOT NULL,
  reference TEXT NOT NULL,
  passage_text TEXT NOT NULL,
  theme_note TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  sent_at TIMESTAMPTZ,
  UNIQUE (challenge_id, day_number)
);

CREATE INDEX IF NOT EXISTS idx_scripture_challenge_plan_challenge
  ON scripture_challenge_plan_items (challenge_id, day_number);

CREATE TABLE IF NOT EXISTS scripture_challenge_messages (
  id BIGSERIAL PRIMARY KEY,
  challenge_id BIGINT NOT NULL REFERENCES scripture_challenges(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scripture_challenge_messages_challenge
  ON scripture_challenge_messages (challenge_id, created_at DESC);

DO $body$
DECLARE
  roles text[] := ARRAY[
    'biblia_bot_user',
    'biblia_bot_user_dev',
    'bot_user',
    'club_db_user'
  ];
  r text;
BEGIN
  FOREACH r IN ARRAY roles LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT ALL ON TABLE scripture_challenges TO %I', r);
      EXECUTE format('GRANT ALL ON TABLE scripture_challenge_plan_items TO %I', r);
      EXECUTE format('GRANT ALL ON TABLE scripture_challenge_messages TO %I', r);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE scripture_challenges_id_seq TO %I', r);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE scripture_challenge_plan_items_id_seq TO %I', r);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE scripture_challenge_messages_id_seq TO %I', r);
    END IF;
  END LOOP;
END $body$;

COMMIT;
