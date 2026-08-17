-- Дата операции (бизнес-день), отдельно от created_at (когда записали в боте).
-- На уже существующей схеме: ALTER + бэкофилл на 2026-08-17.

ALTER TABLE ledger_entries ADD COLUMN IF NOT EXISTS occurred_on DATE;

UPDATE ledger_entries
   SET occurred_on = DATE '2026-08-17'
 WHERE occurred_on IS NULL;

ALTER TABLE ledger_entries ALTER COLUMN occurred_on SET DEFAULT CURRENT_DATE;
ALTER TABLE ledger_entries ALTER COLUMN occurred_on SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ledger_entries_account_occurred
    ON ledger_entries (account_id, occurred_on DESC, id DESC);

COMMENT ON COLUMN ledger_entries.occurred_on IS
    'Дата операции (день по Москве). created_at — момент записи в боте.';
COMMENT ON TABLE ledger_entries IS
    'Журнал: брутто, комиссия и нетто. Удаление откатывает balance_delta на счёт.';
