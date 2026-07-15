-- Факт однократного согласия с юридическими документами (оферта, ПДн, политика).

CREATE TABLE IF NOT EXISTS user_legal_consents (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    bot_variant TEXT,
    telegram_user_id BIGINT NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code TEXT,
    is_premium BOOLEAN,
    is_bot BOOLEAN,
    chat_id BIGINT,
    chat_type TEXT,
    message_id BIGINT,
    callback_query_id TEXT,
    inline_message_id TEXT,
    raw_user_json JSONB,
    raw_chat_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_legal_consents_consented_at
    ON user_legal_consents (consented_at DESC);
