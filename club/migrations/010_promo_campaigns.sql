-- Промо-кампании по deep link /start=promo_<guid> (скидка % на базовые тарифы).

CREATE TABLE IF NOT EXISTS promo_campaigns (
    guid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    discount_percent NUMERIC(5, 2) NOT NULL CHECK (discount_percent > 0 AND discount_percent < 100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT
);

CREATE INDEX IF NOT EXISTS idx_promo_campaigns_active
    ON promo_campaigns (is_active)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS user_promo_assignments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    campaign_guid TEXT NOT NULL REFERENCES promo_campaigns (guid),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at TIMESTAMPTZ,
    consumed_payment_id BIGINT REFERENCES payments (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_promo_active
    ON user_promo_assignments (user_id)
    WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_user_promo_assignments_campaign
    ON user_promo_assignments (campaign_guid);

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS promo_campaign_guid TEXT REFERENCES promo_campaigns (guid);
