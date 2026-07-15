-- Сообщение просьбы в топике дайджеста клуба (для ответа при исполнении).

ALTER TABLE wish_requests
    ADD COLUMN IF NOT EXISTS digest_notice_message_id BIGINT;
