-- Очередь ref_key из attribution без записи в ref_keys (ожидают псевдоним от админа).

CREATE TABLE IF NOT EXISTS ref_key_pending (
    ref_key TEXT PRIMARY KEY,
    sample_touch_key TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    touch_count INT NOT NULL DEFAULT 1,
    admin_notified_at TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ref_key_pending_unnotified
    ON ref_key_pending (first_seen_at)
    WHERE admin_notified_at IS NULL AND dismissed_at IS NULL AND resolved_at IS NULL;
