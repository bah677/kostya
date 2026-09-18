-- Дедуп алертов подарочной волны в админ-топик.
ALTER TABLE gift_campaign_state
    ADD COLUMN IF NOT EXISTS alerts_json JSONB NOT NULL DEFAULT '{}'::jsonb;
