-- Ответы ассистента топика «общение» (дедуп по исходному сообщению юзера).

CREATE TABLE IF NOT EXISTS club_topic_assist_replies (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    thread_id BIGINT NOT NULL,
    source_telegram_message_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    visibility TEXT NOT NULL,
    question_excerpt TEXT,
    answer_text TEXT,
    bot_telegram_message_id BIGINT,
    ephemeral_message_id BIGINT,
    classify_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, source_telegram_message_id)
);

CREATE INDEX IF NOT EXISTS idx_cta_replies_created
    ON club_topic_assist_replies (chat_id, thread_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cta_replies_user
    ON club_topic_assist_replies (user_id, created_at DESC);
