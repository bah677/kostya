-- Снапшоты метрик для ежедневного отчёта и недостающие счётчики донатов (идемпотентно).

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS donation_button INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS donation_proposal_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS metric_snapshots (
  id BIGSERIAL PRIMARY KEY,
  bot_name TEXT NOT NULL DEFAULT 'biblia',
  snapshot_date DATE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  subscribers INTEGER NOT NULL DEFAULT 0,
  dau INTEGER NOT NULL DEFAULT 0,
  mau INTEGER NOT NULL DEFAULT 0,
  messages INTEGER NOT NULL DEFAULT 0,
  avg_messages_per_user DOUBLE PRECISION NOT NULL DEFAULT 0,
  new_users INTEGER NOT NULL DEFAULT 0,
  new_users_30d INTEGER NOT NULL DEFAULT 0,
  new_referrals INTEGER NOT NULL DEFAULT 0,
  new_referrals_30d INTEGER NOT NULL DEFAULT 0,
  donations_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
  donations_month_to_date DOUBLE PRECISION NOT NULL DEFAULT 0,
  donation_proposals INTEGER NOT NULL DEFAULT 0,
  donation_buttons_shown INTEGER NOT NULL DEFAULT 0,
  donation_button_clicks INTEGER NOT NULL DEFAULT 0,
  donations_count INTEGER NOT NULL DEFAULT 0,
  unique_donors INTEGER NOT NULL DEFAULT 0,
  mailing_sent INTEGER NOT NULL DEFAULT 0,
  mailing_success INTEGER NOT NULL DEFAULT 0,
  mailing_failed INTEGER NOT NULL DEFAULT 0,
  UNIQUE (bot_name, snapshot_date)
);

COMMIT;
