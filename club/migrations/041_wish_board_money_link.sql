-- ДДД: связь «просьба ↔ оплата» в базе.
--
-- До сих пор связь жила только в FSM-состоянии (wish_board_wish_id,
-- gift_recipient_user_id). При потере состояния — перезапуск бота, /start,
-- возврат в меню на следующий день — даритель попадал на обычный экран
-- оплаты, деньги уходили на его собственную лицензию, а просьба оставалась
-- висеть «взятой». Насчитано четыре таких платежа от трёх человек,
-- см. docs/wish_board_audit.md.
--
-- Теперь намерение помочь фиксируется в базе в момент отклика, и экран
-- оплаты строится от неё, а не от состояния диалога.
ALTER TABLE wish_requests
  ADD COLUMN IF NOT EXISTS order_id   INTEGER NULL,
  ADD COLUMN IF NOT EXISTS payment_id INTEGER NULL;

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS wish_id BIGINT NULL;

CREATE INDEX IF NOT EXISTS orders_wish_id_idx
  ON orders (wish_id) WHERE wish_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS wish_requests_order_idx
  ON wish_requests (order_id) WHERE order_id IS NOT NULL;

-- Восстанавливаем связь задним числом там, где она однозначна: подарочный
-- заказ с получателем + выполненная просьба той же парой (даритель, проситель).
UPDATE wish_requests w
SET order_id = o.id
FROM orders o
WHERE w.order_id IS NULL
  AND w.status = 'completed'
  AND o.user_id = w.donor_user_id
  AND o.gift_recipient_user_id = w.requester_user_id
  AND o.status = 'paid'
  AND o.paid_at BETWEEN w.taken_at - interval '1 hour'
                    AND coalesce(w.confirmed_at, w.updated_at) + interval '1 hour';
