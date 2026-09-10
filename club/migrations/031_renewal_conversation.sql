-- ПРО-1…6: кэш активности в группе для писем о продлении; пробный тариф → 14 дней.

ALTER TABLE member_profiles
    ADD COLUMN IF NOT EXISTS group_msgs_30d INT NOT NULL DEFAULT 0;

ALTER TABLE member_profiles
    ADD COLUMN IF NOT EXISTS group_msgs_30d_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN member_profiles.group_msgs_30d IS
    'Сообщений участника в клубной группе за 30 дней (без бота); кэш для писем о продлении';
COMMENT ON COLUMN member_profiles.group_msgs_30d_at IS
    'Когда последний раз пересчитали group_msgs_30d';

-- Пробный доступ: 7 → 14 дней, тот же id=5 и те же цены в tariff_prices.
-- type: новое promo_test2weeks; старые deeplink promo_test1week* остаются рабочими в резолверах.
UPDATE tariffs
SET name = '2 недели',
    duration_days = 14,
    type = 'promo_test2weeks'
WHERE id = 5
  AND (type LIKE 'promo_test1week%' OR type = 'promo_test2weeks');
