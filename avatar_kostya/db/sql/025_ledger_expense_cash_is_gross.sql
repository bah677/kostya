-- Расход: брутто = списание с кошелька; нетто = зачисление на сервис.
-- Раньше balance_delta = −(gross+fee) задваивал комиссию; исправляем на −gross.

COMMENT ON COLUMN ledger_entries.balance_delta IS
    'Депозит: +(gross-fee). Расход: −gross (брутто = списание с кошелька; нетто = на сервис).';

UPDATE ledger_entries
SET balance_delta = -amount_gross
WHERE kind = 'expense'
  AND balance_delta <> -amount_gross;

WITH ordered AS (
    SELECT id,
           SUM(balance_delta) OVER (
               PARTITION BY account_id
               ORDER BY occurred_on, id
           ) AS running
    FROM ledger_entries
)
UPDATE ledger_entries e
SET balance_after = o.running
FROM ordered o
WHERE e.id = o.id
  AND e.balance_after IS DISTINCT FROM o.running;

UPDATE ledger_accounts a
SET balance = COALESCE(
    (
        SELECT e.balance_after
        FROM ledger_entries e
        WHERE e.account_id = a.id
        ORDER BY e.occurred_on DESC, e.id DESC
        LIMIT 1
    ),
    0
)
WHERE a.balance IS DISTINCT FROM COALESCE(
    (
        SELECT e.balance_after
        FROM ledger_entries e
        WHERE e.account_id = a.id
        ORDER BY e.occurred_on DESC, e.id DESC
        LIMIT 1
    ),
    0
);
