-- Итеративная редактура caption (full voice / audio short / video short) по reply админа.

CREATE TABLE IF NOT EXISTS caption_edit_sessions (
    id                  UUID PRIMARY KEY,
    entity_type         TEXT NOT NULL,
    -- full_voice | audio_short | video_short
    pending_id          UUID,
    meeting_id          TEXT NOT NULL DEFAULT '',
    chat_id             BIGINT NOT NULL,
    topic_id            BIGINT NOT NULL DEFAULT 0,
    root_message_id     BIGINT NOT NULL,
    -- telegram message_id медиа, на которое отвечают reply
    current_message_id  BIGINT NOT NULL DEFAULT 0,
    -- последнее сообщение с актуальной подписью (после edit/resend)
    media_kind          TEXT NOT NULL DEFAULT '',
    -- voice | video | audio
    title               TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    caption_html        TEXT NOT NULL DEFAULT '',
    context_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- transcript excerpt, clip times, bible quote, recording_kind, etc.
    iterations_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- [{role, content, at}, ...]
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_caption_edit_sessions_root
    ON caption_edit_sessions (chat_id, root_message_id);

CREATE INDEX IF NOT EXISTS idx_caption_edit_sessions_pending
    ON caption_edit_sessions (pending_id)
    WHERE pending_id IS NOT NULL;
