-- Таблица payments в форме, ожидаемой club_ai (PaymentsMixin + PaymentChecker).
-- Перенос строк из легаси donations (если таблица есть), без дублей по (payment_provider, provider_payment_id).

BEGIN;

CREATE TABLE IF NOT EXISTS payments (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  amount DOUBLE PRECISION NOT NULL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  payment_type TEXT NOT NULL DEFAULT 'one_time',
  subscription_id TEXT,
  payment_provider TEXT NOT NULL DEFAULT 'yookassa',
  provider_payment_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  user_telegram_data TEXT,
  order_id BIGINT,
  provider_checkout_url TEXT,
  amount_rub DOUBLE PRECISION,
  exchange_rate DOUBLE PRECISION,
  converted_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_payments_user_created
  ON payments (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payments_pending_created
  ON payments (status, created_at)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_payments_provider_extid
  ON payments (payment_provider, provider_payment_id)
  WHERE provider_payment_id IS NOT NULL;

ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider_checkout_url TEXT;

-- Нормализация статусов донатов → payments
DO $$
DECLARE
  has_ar   boolean;
  has_comp boolean;
  has_upd  boolean;
BEGIN
  IF to_regclass('public.donations') IS NULL THEN
    RAISE NOTICE '[11] donations: таблица отсутствует — перенос пропущен';
    RETURN;
  END IF;

  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'donations' AND column_name = 'amount_rub'
  ) INTO has_ar;
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'donations' AND column_name = 'completed_at'
  ) INTO has_comp;
  SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'donations' AND column_name = 'updated_at'
  ) INTO has_upd;

  EXECUTE format(
    $q$
      INSERT INTO payments (
        user_id, amount, currency, payment_type, subscription_id,
        payment_provider, provider_payment_id, status, user_telegram_data, order_id,
        amount_rub, completed_at, created_at, updated_at
      )
      SELECT
        d.user_id,
        d.amount::double precision,
        COALESCE(NULLIF(trim(d.currency), ''), 'RUB'),
        COALESCE(NULLIF(trim(d.payment_type), ''), 'one_time'),
        NULL::text,
        COALESCE(
          NULLIF(trim(d.payment_provider), ''),
          CASE WHEN upper(trim(COALESCE(d.currency, ''))) = 'USD' THEN 'bzb' ELSE 'yookassa' END
        ),
        NULLIF(trim(d.provider_payment_id), ''),
        CASE lower(trim(COALESCE(d.status::text, 'pending')))
          WHEN 'success' THEN 'succeeded'
          WHEN 'completed' THEN 'succeeded'
          WHEN 'paid' THEN 'succeeded'
          WHEN 'succeeded' THEN 'succeeded'
          WHEN 'pending' THEN 'pending'
          WHEN 'canceled' THEN 'canceled'
          WHEN 'cancelled' THEN 'canceled'
          WHEN 'failed' THEN 'failed'
          ELSE 'pending'
        END,
        CASE WHEN d.user_telegram_data IS NULL THEN NULL ELSE d.user_telegram_data::text END,
        NULL::bigint,
        %s,
        %s,
        COALESCE(d.created_at, NOW()),
        %s
      FROM donations d
      WHERE COALESCE(trim(d.provider_payment_id), '') <> ''
        AND NOT EXISTS (
          SELECT 1 FROM payments p
          WHERE p.payment_provider = COALESCE(
                   NULLIF(trim(d.payment_provider), ''),
                   CASE WHEN upper(trim(COALESCE(d.currency, ''))) = 'USD' THEN 'bzb' ELSE 'yookassa' END
                 )
            AND p.provider_payment_id = trim(d.provider_payment_id)
        )
    $q$,
    CASE WHEN has_ar THEN 'd.amount_rub::double precision' ELSE 'NULL::double precision' END,
    CASE WHEN has_comp THEN 'd.completed_at' ELSE 'NULL::timestamptz' END,
    CASE WHEN has_upd THEN 'd.updated_at' ELSE 'NULL::timestamptz' END
  );

  RAISE NOTICE '[11] donations → payments: импорт выполнен (новые строки только)';
END $$;

COMMIT;
