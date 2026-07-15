-- deactivate_user и другие апдейты ожидают users.updated_at
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

UPDATE users SET updated_at = NOW() WHERE updated_at IS NULL;
