-- Фаза 1: сегменты дожима + пинг для вовлечённых в диалог (статус 110).

ALTER TABLE followup_states
    ADD COLUMN IF NOT EXISTS segment VARCHAR(32),
    ADD COLUMN IF NOT EXISTS last_topic TEXT;

COMMENT ON COLUMN followup_states.segment IS
    'ref_cold | organic_cold | engaged | cart | sensitive (refused → status 998)';
COMMENT ON COLUMN followup_states.last_topic IS
    'Короткая цитата последней темы пользователя для пинга сегмента engaged';

INSERT INTO followup_messages (status, message_text, delay_minutes, is_active)
VALUES (
    110,
    E'{имя}, мы с вами уже говорили — помню: «{тема}».\n\nХочу коротко вернуться к этому, без давления и без оплаты. Напишите <b>уточнить</b> — расскажу, что в клубе по вашей теме, или <b>пока рано</b> — и я не буду напоминать.',
    2880,
    TRUE
)
ON CONFLICT (status) DO UPDATE SET
    message_text = EXCLUDED.message_text,
    delay_minutes = EXCLUDED.delay_minutes,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();
