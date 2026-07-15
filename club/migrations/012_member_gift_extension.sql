-- Подарок продления подписки действующему участнику клуба (донор платит → получатель продлевается).

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS gift_recipient_user_id BIGINT NULL
        REFERENCES users (user_id);

CREATE INDEX IF NOT EXISTS idx_orders_gift_recipient_user_id
    ON orders (gift_recipient_user_id)
    WHERE gift_recipient_user_id IS NOT NULL;
