-- Персональный дневной лимит голосовых молитв (дефолт 2).

INSERT INTO bot_runtime_settings (key, value, updated_at)
VALUES ('prayer_voice_per_user_daily', '2', NOW())
ON CONFLICT (key) DO NOTHING;
