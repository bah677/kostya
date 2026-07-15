-- Маркетинговые касания (multi-campaign) + денормализация first/last touch.

CREATE TABLE IF NOT EXISTS attribution_touches (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    touch_key TEXT NOT NULL,
    touch_kind TEXT NOT NULL,
    source_type TEXT NOT NULL,
    ref_key TEXT,
    channel_type TEXT,
    raw_payload TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attribution_touches_user_created
    ON attribution_touches (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_attribution_touches_ref_key
    ON attribution_touches (ref_key)
    WHERE ref_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_attribution_touches_touch_key
    ON attribution_touches (touch_key);

CREATE INDEX IF NOT EXISTS idx_attribution_touches_created_at
    ON attribution_touches (created_at DESC);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS first_touch_key TEXT,
    ADD COLUMN IF NOT EXISTS first_touch_kind TEXT,
    ADD COLUMN IF NOT EXISTS first_touch_at TIMESTAMPTZ;

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS pay_last_touch_key TEXT,
    ADD COLUMN IF NOT EXISTS pay_last_touch_at TIMESTAMPTZ;
