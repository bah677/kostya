-- История изменений срока лицензии (snapshot остаётся в license одна строка на user_id).

CREATE TABLE IF NOT EXISTS license_history (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    previous_expires_at TIMESTAMPTZ,
    new_expires_at  TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL,
    order_id        BIGINT REFERENCES orders (id),
    payment_id      BIGINT REFERENCES payments (id),
    referred_user_id BIGINT,
    meta            JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_license_history_user_created
    ON license_history (user_id, created_at DESC);

COMMENT ON TABLE license_history IS 'Аудит продлений/создания лицензии; оперативное состояние в license.';
COMMENT ON COLUMN license_history.source IS 'subscription_payment | gift_activation | referral_bonus | ...';
