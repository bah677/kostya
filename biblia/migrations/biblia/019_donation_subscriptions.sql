-- Рекуррентные донаты Biblia (BZB subscriptions).

BEGIN;

CREATE TABLE IF NOT EXISTS donation_subscriptions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  bzb_subscription_id TEXT NOT NULL,
  bzb_payment_link_id TEXT NOT NULL,
  amount DOUBLE PRECISION NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  interval_unit TEXT NOT NULL DEFAULT 'MONTH',
  interval_count INTEGER NOT NULL DEFAULT 1,
  last_charge_at TIMESTAMPTZ,
  next_charge_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  canceled_at TIMESTAMPTZ,
  initial_payment_id BIGINT REFERENCES payments(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_donation_subs_bzb_id
  ON donation_subscriptions (bzb_subscription_id);

CREATE INDEX IF NOT EXISTS idx_donation_subs_user_active
  ON donation_subscriptions (user_id)
  WHERE status IN ('PENDING', 'ACTIVE', 'PAST_DUE');

CREATE INDEX IF NOT EXISTS idx_donation_subs_poll
  ON donation_subscriptions (status)
  WHERE status IN ('PENDING', 'ACTIVE', 'PAST_DUE');

DO $body$
DECLARE
  roles text[] := ARRAY[
    'biblia_bot_user',
    'biblia_bot_user_dev',
    'bot_user',
    'club_db_user'
  ];
  r text;
BEGIN
  FOREACH r IN ARRAY roles LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT ALL ON TABLE donation_subscriptions TO %I', r);
      EXECUTE format(
        'GRANT USAGE, SELECT ON SEQUENCE donation_subscriptions_id_seq TO %I',
        r
      );
    END IF;
  END LOOP;
END $body$;

COMMIT;
