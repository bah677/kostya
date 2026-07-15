-- =========================================================================
-- 00_isolate_dev_users.sql — РАЗОВЫЙ скрипт. ТОЛЬКО для dev-БД.
--
-- ⛔ НА ПРОД (club_db) НЕ КАТИТЬ. Это служебная подготовка тестового
-- контура: оставит активными только перечисленных тестировщиков, всех
-- остальных деактивирует и переведёт их фоновые состояния в терминальные,
-- чтобы при старте бота на dev никакие фоновые таски не пытались слать
-- сообщения / проверять платежи / банить из группы реальных юзеров.
--
-- Конкретно отрабатывает:
--   * users.is_active = FALSE для всех, кроме TESTERS;
--   * followup_states.status -> 999 (терминальный) для всех, кроме TESTERS;
--   * orders.status pending -> cancelled для всех, кроме TESTERS;
--   * payments.status pending -> canceled  для всех, кроме TESTERS;
--   * mailing_audience.status pending -> blocked для всех, кроме TESTERS;
--   * mailing_campaigns в активных статусах -> cancelled
--     (новых рассылок не запускать на dev руками!).
--   * admin_responses pending -> failed для всех, кроме TESTERS.
--
-- НЕ трогает:
--   * messages, conversation_history_legacy — историю не теряем;
--   * license — оставляем как есть, лицензии тестировщиков живые;
--   * support_tickets — поддержка тестируется отдельно.
--
-- TESTERS:
--   367302291  — bog
--   304631563  — tester
--
-- Идемпотентно: повторный запуск ничего лишнего не сделает.
--
-- Использование:
--   PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
--     -v ON_ERROR_STOP=1 -f migrations/00_isolate_dev_users.sql
-- =========================================================================

BEGIN;

-- Список тестировщиков фиксируем во временной таблице, чтобы случайные
-- опечатки в WHERE не помешали идемпотентности.
CREATE TEMP TABLE _testers (user_id BIGINT PRIMARY KEY) ON COMMIT DROP;
INSERT INTO _testers VALUES (367302291), (304631563);

-- 1. Юзеры
UPDATE users
   SET is_active = FALSE
 WHERE is_active = TRUE
   AND user_id NOT IN (SELECT user_id FROM _testers);

-- 2. Followup
UPDATE followup_states
   SET status = 999, updated_at = NOW()
 WHERE status NOT IN (999, 901)
   AND user_id NOT IN (SELECT user_id FROM _testers);

-- 3. Orders pending → cancelled
UPDATE orders
   SET status = 'cancelled'
 WHERE status = 'pending'
   AND user_id NOT IN (SELECT user_id FROM _testers);

-- 4. Payments pending → canceled (в боевой схеме так и пишется через одну "l")
UPDATE payments
   SET status = 'canceled', updated_at = NOW()
 WHERE status = 'pending'
   AND user_id NOT IN (SELECT user_id FROM _testers);

-- 5. Mailing audience pending → blocked
UPDATE mailing_audience
   SET status = 'blocked',
       error  = COALESCE(error, '') || ' [dev: isolated]',
       updated_at = NOW()
 WHERE status = 'pending'
   AND user_id NOT IN (SELECT user_id FROM _testers);

-- 6. Mailing campaigns в активных статусах → cancelled
UPDATE mailing_campaigns
   SET status = 'cancelled', updated_at = NOW()
 WHERE status NOT IN ('completed', 'cancelled', 'archived', 'draft');

-- 7. Admin responses pending → failed
UPDATE admin_responses
   SET status = 'failed',
       error  = COALESCE(error, '') || ' [dev: isolated]',
       updated_at = NOW()
 WHERE status = 'pending'
   AND user_id NOT IN (SELECT user_id FROM _testers);

-- 8. Финальная сводка — что осталось «активным»
DO $$
DECLARE
  v_active_users     INT;
  v_alive_followups  INT;
  v_pending_orders   INT;
  v_pending_payments INT;
  v_pending_audience INT;
  v_pending_admin    INT;
  v_active_camp      INT;
BEGIN
  SELECT count(*) INTO v_active_users     FROM users WHERE is_active = TRUE;
  SELECT count(*) INTO v_alive_followups  FROM followup_states WHERE status NOT IN (999, 901);
  SELECT count(*) INTO v_pending_orders   FROM orders WHERE status = 'pending';
  SELECT count(*) INTO v_pending_payments FROM payments WHERE status = 'pending';
  SELECT count(*) INTO v_pending_audience FROM mailing_audience WHERE status = 'pending';
  SELECT count(*) INTO v_pending_admin    FROM admin_responses WHERE status = 'pending';
  SELECT count(*) INTO v_active_camp      FROM mailing_campaigns
                                          WHERE status NOT IN ('completed','cancelled','archived','draft');
  RAISE NOTICE '----------------------------------------------------------';
  RAISE NOTICE '[dev-isolate] active users               : %', v_active_users;
  RAISE NOTICE '[dev-isolate] non-terminal followups     : %', v_alive_followups;
  RAISE NOTICE '[dev-isolate] pending orders             : %', v_pending_orders;
  RAISE NOTICE '[dev-isolate] pending payments           : %', v_pending_payments;
  RAISE NOTICE '[dev-isolate] pending mailing audience   : %', v_pending_audience;
  RAISE NOTICE '[dev-isolate] pending admin_responses    : %', v_pending_admin;
  RAISE NOTICE '[dev-isolate] non-terminal mailing camps : %', v_active_camp;
  RAISE NOTICE '----------------------------------------------------------';
END $$;

COMMIT;
