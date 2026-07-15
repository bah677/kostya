-- Полная ссылка на страницу оплаты провайдера (YooKassa / BZB), фиксируется при создании платежа в боте.

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS provider_checkout_url TEXT;

COMMENT ON COLUMN payments.provider_checkout_url IS 'URL checkout у провайдера на момент создания (для поддержки и аудита).';
