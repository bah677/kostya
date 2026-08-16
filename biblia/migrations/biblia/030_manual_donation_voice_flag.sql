-- Ручные донаты админов: флаг учёта в пуле голосовых молитв.

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS counts_for_voice_pool BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN payments.counts_for_voice_pool IS
    'Учитывать в формуле лимита бесплатного голоса (sum_succeeded_payments_rub). '
    'false — только отчёты/марафон, без пула.';
