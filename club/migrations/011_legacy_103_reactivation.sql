-- Разовый вывод легаси-лидов (status 103 + диалог) в stuck_dialog.

CREATE TABLE IF NOT EXISTS legacy_103_reactivation (
    user_id BIGINT PRIMARY KEY,
    migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ping_delivered BOOLEAN NOT NULL DEFAULT FALSE,
    skip_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_legacy_103_reactivation_migrated_at
    ON legacy_103_reactivation (migrated_at DESC);
