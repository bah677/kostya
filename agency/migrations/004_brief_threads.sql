-- Связь исходящих brief-сообщений с прогоном (для reply-обсуждения).

CREATE TABLE IF NOT EXISTS brief_messages (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    chat_id         BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    chunk_index     INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, telegram_message_id)
);

CREATE INDEX IF NOT EXISTS idx_brief_messages_run ON brief_messages (run_id);

CREATE TABLE IF NOT EXISTS brief_discussions (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    chat_id         BIGINT NOT NULL,
    reply_to_message_id BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    user_text       TEXT NOT NULL,
    assistant_text  TEXT NOT NULL,
    actions_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brief_discussions_run ON brief_discussions (run_id, created_at DESC);
