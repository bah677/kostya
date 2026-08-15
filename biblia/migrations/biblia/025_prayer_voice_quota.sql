-- Лимит бесплатных голосовых молитв (сутки с 08:00 Europe/Moscow) + разблокировка донатом.

CREATE TABLE IF NOT EXISTS bot_runtime_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO bot_runtime_settings (key, value)
VALUES ('prayer_voice_daily_limit', '50')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS prayer_voice_quota_log (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    quota_day DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prayer_voice_quota_day
    ON prayer_voice_quota_log (quota_day);

CREATE INDEX IF NOT EXISTS idx_prayer_voice_quota_user_day
    ON prayer_voice_quota_log (user_id, quota_day);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS prayer_voice_unlock_pending BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS prayer_last_text TEXT;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS prayer_last_text_at TIMESTAMPTZ;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS purpose TEXT;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agency_ro') THEN
    GRANT SELECT ON TABLE bot_runtime_settings TO agency_ro;
    GRANT SELECT ON TABLE prayer_voice_quota_log TO agency_ro;
    GRANT SELECT ON SEQUENCE prayer_voice_quota_log_id_seq TO agency_ro;
  END IF;
END$$;
