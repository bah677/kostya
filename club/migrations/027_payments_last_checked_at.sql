-- payments: last provider poll timestamp for degrading pending checks
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_payments_pending_poll
    ON payments (status, created_at)
    WHERE status = 'pending';

COMMENT ON COLUMN payments.last_checked_at IS
    'Когда PaymentChecker последний раз опросил статус у агрегатора (для деградирующего интервала).';
