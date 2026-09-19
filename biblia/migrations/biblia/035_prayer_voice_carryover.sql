-- Перенос неиспользованных слотов голосовых молитв на следующие квотные сутки.
ALTER TABLE prayer_voice_period
    ADD COLUMN IF NOT EXISTS carryover_slots INTEGER NOT NULL DEFAULT 0;

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['biblia_bot_user']
    LOOP
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE ON TABLE prayer_voice_period TO %I', r
        );
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'grant skip: %', SQLERRM;
END $$;
