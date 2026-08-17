-- Один раз: все уже существующие операции — на 17.08.2026.
-- Повторный запуск не трогает записи, добавленные после этого.

CREATE TABLE IF NOT EXISTS ledger_meta (
    key         TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM ledger_meta WHERE key = 'occurred_on_all_2026_08_17'
  ) THEN
    UPDATE ledger_entries SET occurred_on = DATE '2026-08-17';
    INSERT INTO ledger_meta (key) VALUES ('occurred_on_all_2026_08_17');
  END IF;
END $$;
