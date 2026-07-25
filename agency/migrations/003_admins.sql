-- Админы agency-бота (доступ к командам). Супер-админ — только из .env SUPER_ADMIN_ID.

CREATE TABLE IF NOT EXISTS admins (
    telegram_user_id BIGINT PRIMARY KEY,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT
);

CREATE INDEX IF NOT EXISTS idx_admins_created ON admins (created_at);
