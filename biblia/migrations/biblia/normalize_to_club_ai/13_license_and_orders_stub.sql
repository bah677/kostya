-- Минимальные orders / tariffs; license — создать или дополнить.

BEGIN;

CREATE TABLE IF NOT EXISTS tariffs (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL DEFAULT 'legacy',
  type TEXT DEFAULT 'base',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO tariffs (name, type)
SELECT 'legacy', 'base'
WHERE NOT EXISTS (SELECT 1 FROM tariffs LIMIT 1);

CREATE TABLE IF NOT EXISTS orders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  tariff_id BIGINT REFERENCES tariffs (id),
  currency TEXT DEFAULT 'RUB',
  amount NUMERIC,
  amount_rub NUMERIC,
  is_gift BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  paid_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (user_id, created_at DESC);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'license'
  ) THEN
    CREATE TABLE license (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
      license_type TEXT,
      expires_at TIMESTAMPTZ,
      payment_id BIGINT,
      status TEXT NOT NULL DEFAULT 'active',
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uq_license_user_id ON license (user_id);
  ELSE
    ALTER TABLE license ADD COLUMN IF NOT EXISTS license_type TEXT;
    ALTER TABLE license ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
    ALTER TABLE license ADD COLUMN IF NOT EXISTS payment_id BIGINT;
    ALTER TABLE license ADD COLUMN IF NOT EXISTS status TEXT;
    ALTER TABLE license ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
    ALTER TABLE license ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
    CREATE UNIQUE INDEX IF NOT EXISTS uq_license_user_id ON license (user_id);
  END IF;
END $$;

COMMIT;
