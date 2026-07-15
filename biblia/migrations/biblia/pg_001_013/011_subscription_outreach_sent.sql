-- Идемпотентность рассылок subscription_reminder: не чаще одного раза (slug × календарный день × user).

CREATE TABLE IF NOT EXISTS subscription_outreach_sent (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    outreach_slug TEXT NOT NULL,
    sent_on_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, outreach_slug, sent_on_date)
);

CREATE INDEX IF NOT EXISTS idx_subscription_outreach_user_date
    ON subscription_outreach_sent (user_id, sent_on_date DESC);

COMMENT ON TABLE subscription_outreach_sent IS 'Отметки отправки напоминаний/бот-сообщений по подписке (анти-дубль при повторном cron).';
