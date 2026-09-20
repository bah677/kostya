-- ДДД: тестовые метки, напоминания дарителю/модерации, username получателя подарка.
ALTER TABLE wish_requests
  ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS donor_reminded_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS moderation_reminded_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS no_donor_notified_at TIMESTAMPTZ NULL;

ALTER TABLE gifts
  ADD COLUMN IF NOT EXISTS recipient_username TEXT NULL;

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS gift_recipient_username TEXT NULL;

UPDATE wish_requests SET is_test = TRUE
WHERE requester_user_id IN (304631563, 8815327756);
