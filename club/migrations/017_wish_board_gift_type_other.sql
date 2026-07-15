-- Переименование типа просьбы: immaterial → other (прочая помощь, не подписка).

UPDATE wish_requests
SET gift_type = 'other', updated_at = NOW()
WHERE gift_type = 'immaterial';
