-- Старый Biblia: первичного ключа на messages могло не быть; колонка message_id —
-- не telegram id и не BIGINT id для дедупа. Для club_ai нужен BIGINT id и PK.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'messages'
      AND column_name = 'id'
  ) THEN
    ALTER TABLE messages ADD COLUMN id BIGSERIAL PRIMARY KEY;
    RAISE NOTICE '[15] messages.id BIGSERIAL PRIMARY KEY добавлен';
  ELSE
    RAISE NOTICE '[15] messages.id уже есть — пропуск';
  END IF;
END $$;

COMMIT;
