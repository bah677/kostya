-- Legacy admins: user_id → telegram_user_id (единая схема для club-style кода).

ALTER TABLE admins ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT;

UPDATE admins
SET telegram_user_id = user_id
WHERE telegram_user_id IS NULL
  AND user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_admins_telegram_user_id
  ON admins (telegram_user_id)
  WHERE telegram_user_id IS NOT NULL;
