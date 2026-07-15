-- Фаза 0: тексты дожима, задержки, второе напоминание об оплате (статус 202).

UPDATE followup_messages
SET
    message_text = E'{имя}, вы заглянули в бота — но мы так и не поговорили.\n\nЧто сейчас ближе: <b>отношения</b>, <b>деньги/работа</b> или <b>усталость</b>?\n\nНапишите одно слово — расскажу, что в клубе есть именно по этой теме. Без обязаловки.',
    delay_minutes = 45,
    updated_at = NOW()
WHERE status = 101;

UPDATE followup_messages
SET
    message_text = E'{имя}, добрый вечер.\n\nЕсли день был плотный — можно ответить одним словом: <b>отношения</b> / <b>деньги</b> / <b>усталость</b>.\n\nЯ подскажу, с чего там обычно начинают, и вы сами решите, нужно ли вам это сейчас.',
    delay_minutes = 30,
    updated_at = NOW()
WHERE status = 102;

UPDATE followup_messages
SET
    message_text = E'{имя}, вижу, что вы начали оформление, но оплата не прошла.\n\nЧасто мешает кнопка или «а вдруг не моё».\n\nВыберите вариант ниже 👇',
    delay_minutes = 30,
    updated_at = NOW()
WHERE status = 201;

INSERT INTO followup_messages (status, message_text, delay_minutes, is_active)
VALUES (
    202,
    E'{имя}, вчера вы смотрели клуб, но не дошли до оплаты.\n\nЕсли всё ещё актуально — нажмите кнопку ниже. Если нет — напишите <b>стоп</b>, больше не напомню.',
    1440,
    TRUE
)
ON CONFLICT (status) DO UPDATE SET
    message_text = EXCLUDED.message_text,
    delay_minutes = EXCLUDED.delay_minutes,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();
