-- Черновик ответа ИИ для тикетов поддержки.

ALTER TABLE support_tickets
    ADD COLUMN IF NOT EXISTS ai_draft_response TEXT,
    ADD COLUMN IF NOT EXISTS ai_draft_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ai_draft_message_id BIGINT;

COMMENT ON COLUMN support_tickets.ai_draft_response IS 'Черновик ответа для админа (DeepSeek)';
COMMENT ON COLUMN support_tickets.ai_draft_message_id IS 'message_id поста с черновиком в админ-канале';
