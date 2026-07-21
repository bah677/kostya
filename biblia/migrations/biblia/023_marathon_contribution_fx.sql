-- Кросс-курсы марафона в contributions (payments не меняем).

BEGIN;

ALTER TABLE donation_marathon_contributions
  ADD COLUMN IF NOT EXISTS goal_currency TEXT,
  ADD COLUMN IF NOT EXISTS amount_rub NUMERIC(14, 2),
  ADD COLUMN IF NOT EXISTS rub_per_goal_unit NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS rate_original_to_goal NUMERIC(18, 8),
  ADD COLUMN IF NOT EXISTS fx_source TEXT;

COMMENT ON COLUMN donation_marathon_contributions.amount_goal IS
  'Сумма взноса в валюте цели марафона';
COMMENT ON COLUMN donation_marathon_contributions.amount_rub IS
  'Рублёвый эквивалент на момент учёта (из payments.amount_rub или расчёт); payments не трогаем';
COMMENT ON COLUMN donation_marathon_contributions.rub_per_goal_unit IS
  'Сколько RUB за 1 единицу валюты цели (курс ЦБ), использованный для перевода';
COMMENT ON COLUMN donation_marathon_contributions.rate_original_to_goal IS
  'Сколько единиц валюты цели за 1 единицу исходной валюты';
COMMENT ON COLUMN donation_marathon_contributions.fx_source IS
  'same_currency | cbr | usdt_eq_usd | manual';

-- Для уже существующих строк подтянуть валюту цели из марафона.
UPDATE donation_marathon_contributions c
   SET goal_currency = m.goal_currency
  FROM donation_marathons m
 WHERE c.marathon_id = m.id
   AND c.goal_currency IS NULL;

COMMIT;
