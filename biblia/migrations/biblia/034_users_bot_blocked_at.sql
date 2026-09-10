-- Когда узнали, что пользователь заблокировал бота (Telegram Forbidden / chat not found).
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS bot_blocked_at timestamptz NULL;

COMMENT ON COLUMN users.bot_blocked_at IS
  'Момент, когда бот впервые/в последний раз узнал, что пользователь его заблокировал';

UPDATE users
SET bot_blocked_at = COALESCE(updated_at, created_at, NOW())
WHERE is_active IS FALSE
  AND bot_blocked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_bot_blocked_at
  ON users (bot_blocked_at DESC)
  WHERE bot_blocked_at IS NOT NULL;
