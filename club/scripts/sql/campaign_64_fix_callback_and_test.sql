-- Кампания 64: колбэк promo_week → payment_start_promo_test1week_20260715EfirNoy
-- + тестовая копия только для admin 304631563 (created_by кампании 64).

BEGIN;

UPDATE mailing_campaigns
SET buttons = jsonb_build_array(
        jsonb_build_object(
            'text', 'Тестовая неделя 299₽',
            'style', 'success',
            'callback', 'payment_start_promo_test1week_20260715EfirNoy'
        )
    ),
    updated_at = NOW()
WHERE id = 64;

WITH src AS (
    SELECT * FROM mailing_campaigns WHERE id = 64
),
ins AS (
    INSERT INTO mailing_campaigns (
        name,
        text,
        parse_mode,
        scheduled_at,
        status,
        has_ref_link,
        media_type,
        media_file_id,
        created_by,
        buttons,
        attachments
    )
    SELECT
        '[TEST] ' || src.name,
        src.text,
        src.parse_mode,
        NOW() - INTERVAL '1 minute',
        'planned',
        src.has_ref_link,
        src.media_type,
        src.media_file_id,
        src.created_by,
        jsonb_build_array(
            jsonb_build_object(
                'text', 'Тестовая неделя 299₽',
                'style', 'success',
                'callback', 'payment_start_promo_test1week_20260715EfirNoy'
            )
        ),
        src.attachments
    FROM src
    RETURNING id, created_by
)
INSERT INTO mailing_audience (campaign_id, user_id)
SELECT ins.id, ins.created_by
FROM ins;

COMMIT;

-- Проверка
SELECT id, name, status, scheduled_at, buttons
FROM mailing_campaigns
WHERE id = 64 OR name LIKE '[TEST] эфир про Ноя-2%'
ORDER BY id;

SELECT campaign_id, user_id, status
FROM mailing_audience
WHERE campaign_id IN (
    SELECT id FROM mailing_campaigns WHERE name LIKE '[TEST] эфир про Ноя-2%'
);
