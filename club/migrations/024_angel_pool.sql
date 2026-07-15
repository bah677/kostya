-- Ангельский взнос: один платёж → случайные продления участникам с ≤3 дней подписки.

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS is_angel_pool BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS angel_pool_slots INTEGER NULL;

CREATE INDEX IF NOT EXISTS idx_orders_angel_pool
    ON orders (id)
    WHERE is_angel_pool = TRUE;

CREATE TABLE IF NOT EXISTS angel_pool_recipients (
    id              BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders (id),
    payment_id      BIGINT NULL REFERENCES payments (id),
    donor_user_id   BIGINT NOT NULL REFERENCES users (user_id),
    recipient_user_id BIGINT NOT NULL REFERENCES users (user_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_angel_pool_recipients_recipient
    ON angel_pool_recipients (recipient_user_id);

CREATE INDEX IF NOT EXISTS idx_angel_pool_recipients_order
    ON angel_pool_recipients (order_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_angel_pool_recipients_order_recipient
    ON angel_pool_recipients (order_id, recipient_user_id);
