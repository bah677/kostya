-- Компенсационные пропуски голосовых молитв (вне суточного лимита).
-- Общий механизм: users.prayer_voice_bonus — сколько раз можно озвучить сверх дневной квоты.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS prayer_voice_bonus INTEGER NOT NULL DEFAULT 0;

-- Разовая выдача +1 тем, кого задел сбой ElevenLabs 14–15.08.2026 (quota_exceeded)
-- и кто после этого задонатил. Идемпотентно через флаг в bot_runtime_settings.
DO $body$
BEGIN
  IF to_regclass('public.bot_runtime_settings') IS NULL THEN
    RAISE NOTICE '[027] bot_runtime_settings missing — skip bonus grant';
    RETURN;
  END IF;

  IF EXISTS (
    SELECT 1 FROM bot_runtime_settings
     WHERE key = 'bonus_grant_tts_outage_20260814'
  ) THEN
    RAISE NOTICE '[027] bonus already granted — skip';
    RETURN;
  END IF;

  UPDATE users
     SET prayer_voice_bonus = COALESCE(prayer_voice_bonus, 0) + 1
   WHERE user_id IN (
     1455453288,
     1705999706,
     6157745436,
     539366031,
     686702242,
     1241623597,
     1096245678,
     767904530
   );

  INSERT INTO bot_runtime_settings (key, value, updated_at)
  VALUES ('bonus_grant_tts_outage_20260814', '1', NOW())
  ON CONFLICT (key) DO NOTHING;
END $body$;
