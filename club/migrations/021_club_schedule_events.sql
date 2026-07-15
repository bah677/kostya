-- Расписание клуба (эфиры, молитвы и т.д.) для member-агента.

CREATE TABLE IF NOT EXISTS club_schedule_events (
    id SERIAL PRIMARY KEY,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ,
    title TEXT NOT NULL,
    content_type VARCHAR(32) NOT NULL DEFAULT 'other',
    source VARCHAR(16) NOT NULL DEFAULT 'group_message',
    source_message_id BIGINT,
    source_chat_id BIGINT,
    source_admin_id BIGINT,
    group_message_link TEXT,
    raw_text TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    is_cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_club_schedule_starts
    ON club_schedule_events (starts_at)
    WHERE NOT is_cancelled;

CREATE INDEX IF NOT EXISTS idx_club_schedule_admin_source
    ON club_schedule_events (source_admin_id, created_at DESC);
