-- Марафоны сбора пожертвований (цель + взносы + привязка payments).

BEGIN;

CREATE TABLE IF NOT EXISTS donation_marathons (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  description_html TEXT NOT NULL,
  goal_amount NUMERIC(14, 2) NOT NULL CHECK (goal_amount > 0),
  goal_currency TEXT NOT NULL CHECK (goal_currency IN ('RUB', 'USD', 'EUR')),
  accept_rub BOOLEAN NOT NULL DEFAULT FALSE,
  accept_usd BOOLEAN NOT NULL DEFAULT FALSE,
  accept_crypto BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'completed', 'cancelled')),
  created_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at TIMESTAMPTZ,
  close_reason TEXT,
  CONSTRAINT donation_marathons_accept_any CHECK (
    accept_rub OR accept_usd OR accept_crypto
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_donation_marathons_one_active
  ON donation_marathons ((1))
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS donation_marathon_contributions (
  id BIGSERIAL PRIMARY KEY,
  marathon_id BIGINT NOT NULL REFERENCES donation_marathons(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  amount_goal NUMERIC(14, 2) NOT NULL CHECK (amount_goal > 0),
  amount_original NUMERIC(14, 2),
  currency_original TEXT,
  payment_id BIGINT REFERENCES payments(id),
  source TEXT NOT NULL CHECK (source IN ('payment', 'crypto_manual')),
  note TEXT,
  created_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_marathon_contrib_payment
  ON donation_marathon_contributions (payment_id)
  WHERE payment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_marathon_contrib_marathon
  ON donation_marathon_contributions (marathon_id);

ALTER TABLE payments
  ADD COLUMN IF NOT EXISTS marathon_id BIGINT REFERENCES donation_marathons(id);

CREATE INDEX IF NOT EXISTS idx_payments_marathon_id
  ON payments (marathon_id)
  WHERE marathon_id IS NOT NULL;

DO $body$
DECLARE
  roles text[] := ARRAY[
    'biblia_bot_user',
    'biblia_bot_user_dev',
    'bot_user',
    'appuser'
  ];
  r text;
BEGIN
  FOREACH r IN ARRAY roles LOOP
    BEGIN
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON donation_marathons TO %I', r);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE donation_marathons_id_seq TO %I', r);
      EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON donation_marathon_contributions TO %I', r
      );
      EXECUTE format(
        'GRANT USAGE, SELECT ON SEQUENCE donation_marathon_contributions_id_seq TO %I', r
      );
    EXCEPTION WHEN undefined_object THEN
      NULL;
    END;
  END LOOP;
END
$body$;

COMMIT;
