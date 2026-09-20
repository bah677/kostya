-- Режим отображения прогресса марафона: деньги или молитвы (без сумм в UI).
ALTER TABLE donation_marathons
    ADD COLUMN IF NOT EXISTS progress_display TEXT NOT NULL DEFAULT 'money';

ALTER TABLE donation_marathons
    DROP CONSTRAINT IF EXISTS donation_marathons_progress_display_check;

ALTER TABLE donation_marathons
    ADD CONSTRAINT donation_marathons_progress_display_check
    CHECK (progress_display IN ('money', 'prayers'));

ALTER TABLE donation_marathons
    ADD COLUMN IF NOT EXISTS goal_prayers INTEGER;

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['biblia_bot_user', 'biblia_bot_user_dev', 'bot_user', 'appuser']
    LOOP
        BEGIN
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON donation_marathons TO %I', r
            );
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END;
    END LOOP;
END $$;
