-- Эфиры и расписание (ТЗ ЭФ-1…ЭФ-6, без сплита ЭФ-5).

-- Повторы (ЭФ-1)
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS recurrence TEXT NOT NULL DEFAULT 'none';
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS recurrence_dow INT;
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS recurrence_until DATE;
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS series_id INT
        REFERENCES club_schedule_events (id) ON DELETE SET NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'club_schedule_events_recurrence_chk'
    ) THEN
        ALTER TABLE club_schedule_events
            ADD CONSTRAINT club_schedule_events_recurrence_chk
            CHECK (recurrence = ANY (ARRAY['none', 'daily', 'weekly']));
    END IF;
END $$;

-- Запись эфира (ЭФ-3)
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS recording_url TEXT;
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS recording_ready_at TIMESTAMPTZ;
ALTER TABLE club_schedule_events
    ADD COLUMN IF NOT EXISTS recording_requested_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_club_schedule_series
    ON club_schedule_events (series_id)
    WHERE series_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_club_schedule_recurrence
    ON club_schedule_events (recurrence)
    WHERE recurrence <> 'none' AND NOT is_cancelled;

CREATE INDEX IF NOT EXISTS idx_club_schedule_recording_pending
    ON club_schedule_events (ends_at)
    WHERE recording_url IS NULL
      AND NOT is_cancelled
      AND content_type = ANY (ARRAY['air', 'qa', 'repentance', 'other']);

-- Учёт касаний по эфиру (ЭФ-2 / ЭФ-3 / ЭФ-6)
CREATE TABLE IF NOT EXISTS air_invite_sends (
    id          BIGSERIAL PRIMARY KEY,
    event_id    INT NOT NULL REFERENCES club_schedule_events (id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL
                CHECK (kind = ANY (ARRAY['invite', 'reminder', 'recording'])),
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT air_invite_sends_uniq UNIQUE (event_id, user_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_air_invite_sends_user_day
    ON air_invite_sends (user_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_air_invite_sends_event_kind
    ON air_invite_sends (event_id, kind);

-- Состояние сторожа горизонта / связности
CREATE TABLE IF NOT EXISTS schedule_ops_state (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
