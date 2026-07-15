-- Baseline schema as of 2026-05 (прод и dev выровнены).
-- Бывшие отдельные шаги 001_admins + 002_mailing_attachments + 003_report_snapshots объединены.
-- Новые изменения схемы — отдельными файлами 002_*.sql, 003_*.sql, … и правкой apply_all_db_migrations.sh.

-- Telegram user IDs allowed to use in-bot admin features (admin supergroup handlers).
-- Seed: INSERT INTO admins (telegram_user_id) VALUES (<your_id>) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_admins_created_at ON admins (created_at DESC);

-- Мультимедиа в кампании: JSONB массив вида [{"type":"photo","file_id":"..."}, ...]
ALTER TABLE mailing_campaigns
  ADD COLUMN IF NOT EXISTS attachments JSONB DEFAULT NULL;

COMMENT ON COLUMN mailing_campaigns.attachments IS
  'Несколько вложений порядком; когда задано, приоритет над media_type/media_file_id.';

-- Ежедневные снепшоты отчёта клуба для /report и /graf.
-- Источник заполнения: AdminConsoleFeature (cron и ручной /report).

CREATE TABLE IF NOT EXISTS club_report_snapshots (
    snapshot_date DATE PRIMARY KEY,
    total_users INTEGER NOT NULL DEFAULT 0,
    active_users INTEGER NOT NULL DEFAULT 0,
    new_users INTEGER NOT NULL DEFAULT 0,
    pending_orders INTEGER NOT NULL DEFAULT 0,
    pending_unique_users INTEGER NOT NULL DEFAULT 0,
    paid_orders INTEGER NOT NULL DEFAULT 0,
    paid_unique_users INTEGER NOT NULL DEFAULT 0,
    total_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    month_paid_orders INTEGER NOT NULL DEFAULT 0,
    month_unique_users INTEGER NOT NULL DEFAULT 0,
    month_total_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    active_licenses INTEGER NOT NULL DEFAULT 0,
    users_expired INTEGER NOT NULL DEFAULT 0,
    report_html TEXT NOT NULL DEFAULT '',
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL DEFAULT 'runtime',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_club_report_snapshots_created_at
    ON club_report_snapshots (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_club_report_snapshots_total_amount
    ON club_report_snapshots (total_amount DESC);
