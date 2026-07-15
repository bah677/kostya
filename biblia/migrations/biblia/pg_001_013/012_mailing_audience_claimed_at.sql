-- Рассылки: наблюдаемость захвата строк и восстановление после падения процесса.
-- Код ставит mailing_audience.status = 'processing' атомарно при claim батча;
-- анти-дубль при нескольких инстансах бота без удержания транзакции на время отправки в Telegram.

ALTER TABLE mailing_audience
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

COMMENT ON COLUMN mailing_audience.claimed_at IS 'Момент последнего claim (processing); обновляется при ретраях; очистка в терминальных статусах';

-- Если столбец `mailing_audience.status` ограничен ENUM без значения `processing`,
-- добавьте вручную, например: ALTER TYPE имя_enum ADD VALUE IF NOT EXISTS 'processing';
