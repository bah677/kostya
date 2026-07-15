-- Сегмент 1: «застряли в диалоге» — статусы 120 (ожидание пинга), 121 (пинг отправлен), 122 (завершено).

ALTER TABLE followup_states
    ADD COLUMN IF NOT EXISTS stuck_context JSONB,
    ADD COLUMN IF NOT EXISTS last_assistant_at TIMESTAMPTZ;

COMMENT ON COLUMN followup_states.stuck_context IS
    'Кэш LLM+RAG для stuck_dialog: analysis, rag, composed_answer, flags';
COMMENT ON COLUMN followup_states.last_assistant_at IS
    'Время последнего ответа ассистента в личке (якорь таймера 24–48 ч)';

INSERT INTO followup_messages (status, message_text, delay_minutes, is_active)
VALUES (
    120,
    E'{имя}, вы спросили про {тема}.\n\nЯ подготовил для вас короткую выжимку того, что говорят в клубе по этой теме. Это бесплатно и без подписки.\n\n👇 Нажмите «Получить ответ», и я пришлю короткий разбор по материалам клуба.',
    1440,
    TRUE
)
ON CONFLICT (status) DO UPDATE SET
    message_text = EXCLUDED.message_text,
    delay_minutes = EXCLUDED.delay_minutes,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();
