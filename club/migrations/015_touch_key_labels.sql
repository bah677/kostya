-- Псевдонимы для маркетинговых touch_key (колбэки оплаты, promo_week и т.д.).

CREATE TABLE IF NOT EXISTS touch_key_labels (
    touch_key TEXT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(255),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS touch_key_pending (
    id SERIAL PRIMARY KEY,
    touch_key TEXT UNIQUE NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    touch_count INT NOT NULL DEFAULT 1,
    admin_notified_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_touch_key_pending_unnotified
    ON touch_key_pending (first_seen_at)
    WHERE admin_notified_at IS NULL AND dismissed_at IS NULL AND resolved_at IS NULL;
