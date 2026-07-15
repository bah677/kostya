-- Тип license_type = admin_subscription: постоянный доступ команды (без цепочки напоминаний).

-- Супер-админ клуба (304631563): админская подписка до 2099-12-31.
INSERT INTO license (user_id, license_type, expires_at, status)
VALUES (304631563, 'admin_subscription', '2099-12-31 23:59:59+03', 'active')
ON CONFLICT (user_id) DO UPDATE SET
    license_type = EXCLUDED.license_type,
    expires_at = EXCLUDED.expires_at,
    status = 'active',
    updated_at = NOW();

INSERT INTO license_history (
    user_id,
    previous_expires_at,
    new_expires_at,
    source,
    meta
)
SELECT
    304631563,
    NULL,
    '2099-12-31 23:59:59+03',
    'admin_subscription_grant',
    '{"note": "migration 013 admin subscription"}'::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM license_history
    WHERE user_id = 304631563
      AND source = 'admin_subscription_grant'
);
