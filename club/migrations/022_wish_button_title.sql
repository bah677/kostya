-- Короткое название просьбы для кнопок в списке (генерируется LLM).

ALTER TABLE wish_requests
    ADD COLUMN IF NOT EXISTS button_title VARCHAR(64);
