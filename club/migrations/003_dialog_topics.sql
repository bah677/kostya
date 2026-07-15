-- Персональные топики диалогов: один форум-топик на пользователя
-- в специальной Telegram-супергруппе (DIALOG_FORUM_GROUP_ID).
CREATE TABLE IF NOT EXISTS dialog_topics (
    user_id    BIGINT PRIMARY KEY,
    topic_id   INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dialog_topics_topic
    ON dialog_topics (topic_id);
