-- Разовый отчёт: скорость реакции на ВНЕШНИЕ кампании (ref_keys) «Вар1–Вар6» по слотам.
--
-- Когорта: ref_keys с «Вар» в name (напр. «Библия Бот – Вар1 сб 20 мск») или ref_key 20260516_/20260517_*.
-- Якорь: время слота из ref_key (20260516 → сб 20:00, 20260517_*_1/2/3 → вск 9/14/21) или slot_send ниже.
-- Касания: attribution_touches + interaction_logs + messages.
--
-- psql "$DATABASE_URL" -f scripts/sql/ref_reaction_latency_by_slot.sql

WITH slot_send AS (
    SELECT
        send_slot,
        (send_at_local AT TIME ZONE 'Europe/Moscow') AS send_at
    FROM (
        VALUES
            ('сб 20 мск', '2026-05-16 20:00:00'::timestamp),
            ('вск 9 мск',  '2026-05-17 09:00:00'::timestamp),
            ('вск 14 мск', '2026-05-17 14:00:00'::timestamp),
            ('вск 21 мск', '2026-05-17 21:00:00'::timestamp)
    ) AS v(send_slot, send_at_local)
),
ref_by_slot AS (
    SELECT
        rk.ref_key,
        rk.name,
        CASE
            WHEN COALESCE(rk.name, rk.ref_key) ILIKE '%сб 20 мск%' THEN 'сб 20 мск'
            WHEN COALESCE(rk.name, rk.ref_key) ILIKE '%вск 9 мск%' THEN 'вск 9 мск'
            WHEN COALESCE(rk.name, rk.ref_key) ILIKE '%вск 14 мск%' THEN 'вск 14 мск'
            WHEN COALESCE(rk.name, rk.ref_key) ILIKE '%вск 21 мск%' THEN 'вск 21 мск'
            WHEN rk.ref_key ~ '^20260516_' THEN 'сб 20 мск'
            WHEN rk.ref_key ~ '^20260517_.*_1$' THEN 'вск 9 мск'
            WHEN rk.ref_key ~ '^20260517_.*_2$' THEN 'вск 14 мск'
            WHEN rk.ref_key ~ '^20260517_.*_3$' THEN 'вск 21 мск'
        END AS send_slot,
        CASE
            WHEN rk.ref_key ~ '^20260516_'
                THEN (TIMESTAMP '2026-05-16 20:00:00' AT TIME ZONE 'Europe/Moscow')
            WHEN rk.ref_key ~ '^20260517_.*_1$'
                THEN (TIMESTAMP '2026-05-17 09:00:00' AT TIME ZONE 'Europe/Moscow')
            WHEN rk.ref_key ~ '^20260517_.*_2$'
                THEN (TIMESTAMP '2026-05-17 14:00:00' AT TIME ZONE 'Europe/Moscow')
            WHEN rk.ref_key ~ '^20260517_.*_3$'
                THEN (TIMESTAMP '2026-05-17 21:00:00' AT TIME ZONE 'Europe/Moscow')
        END AS send_at_from_key
    FROM ref_keys rk
    WHERE COALESCE(rk.name, '') ILIKE '%Вар%'
       OR rk.ref_key ~ '^2026051[67]_'
),
ref_scoped AS (
    SELECT
        r.ref_key,
        r.name,
        r.send_slot,
        COALESCE(r.send_at_from_key, ss.send_at) AS send_at
    FROM ref_by_slot r
    LEFT JOIN slot_send ss ON ss.send_slot = r.send_slot
    WHERE r.send_slot IS NOT NULL
      AND COALESCE(r.send_at_from_key, ss.send_at) IS NOT NULL
),
touch_events AS (
    SELECT at.user_id, at.ref_key, at.created_at
    FROM attribution_touches at
    WHERE at.ref_key IS NOT NULL

    UNION ALL

    SELECT
        il.user_id,
        SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') AS ref_key,
        il.created_at
    FROM interaction_logs il
    WHERE il.data->>'text' LIKE '%/start ref_%'
      AND SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') IS NOT NULL

    UNION ALL

    SELECT
        m.user_id,
        SUBSTRING(m.content FROM 'ref_([a-zA-Z0-9_]+)') AS ref_key,
        m.created_at
    FROM messages m
    WHERE m.content ILIKE '/start%ref_%'
      AND m.deleted_at IS NULL
      AND (
          m.role = 'user'
          OR m.sender_type = 'user'
      )
      AND (m.chat_id = m.user_id OR m.chat_id > 0)
),
first_touch AS (
    SELECT DISTINCT ON (r.send_slot, te.user_id)
        r.send_slot,
        te.user_id,
        te.created_at AS touched_at,
        r.send_at
    FROM touch_events te
    JOIN ref_scoped r ON r.ref_key = te.ref_key
    WHERE te.created_at >= r.send_at
    ORDER BY r.send_slot, te.user_id, te.created_at ASC
),
bucketed AS (
    SELECT
        ft.send_slot,
        ft.user_id,
        ft.send_at,
        ft.touched_at,
        EXTRACT(EPOCH FROM (ft.touched_at - ft.send_at)) / 3600.0 AS hours_after_send,
        CASE
            WHEN ft.touched_at < ft.send_at + INTERVAL '1 hour' THEN 'зашли в 1-й час'
            WHEN ft.touched_at < ft.send_at + INTERVAL '3 hours' THEN 'зашли 1–3 ч'
            WHEN ft.touched_at < ft.send_at + INTERVAL '6 hours' THEN 'зашли 3–6 ч'
            ELSE 'зашли 6+ ч'
        END AS reaction_bucket
    FROM first_touch ft
),
agg AS (
    SELECT
        send_slot,
        COUNT(*)::int AS entered,
        COUNT(*) FILTER (WHERE reaction_bucket = 'зашли в 1-й час')::int AS h1,
        COUNT(*) FILTER (WHERE reaction_bucket = 'зашли 1–3 ч')::int AS h1_3,
        COUNT(*) FILTER (WHERE reaction_bucket = 'зашли 3–6 ч')::int AS h3_6,
        COUNT(*) FILTER (WHERE reaction_bucket = 'зашли 6+ ч')::int AS h6p,
        ROUND(
            100.0 * COUNT(*) FILTER (WHERE reaction_bucket = 'зашли в 1-й час')
            / NULLIF(COUNT(*), 0),
            1
        ) AS pct_h1,
        ROUND(AVG(hours_after_send)::numeric, 2) AS avg_hours
    FROM bucketed
    GROUP BY send_slot
)
SELECT
    s.send_slot AS "слот отправки",
    COALESCE(a.entered, 0) AS "перешли по ссылке",
    COALESCE(a.h1, 0) AS "зашли в 1-й час",
    COALESCE(a.h1_3, 0) AS "зашли 1–3 ч",
    COALESCE(a.h3_6, 0) AS "зашли 3–6 ч",
    COALESCE(a.h6p, 0) AS "зашли 6+ ч",
    COALESCE(a.entered, 0) AS "сумма с реакцией",
    COALESCE(a.pct_h1, 0) AS "% в 1-й час",
    COALESCE(a.avg_hours, 0) AS "ср. часов до перехода"
FROM slot_send s
LEFT JOIN agg a ON a.send_slot = s.send_slot
ORDER BY
    CASE s.send_slot
        WHEN 'сб 20 мск' THEN 1
        WHEN 'вск 9 мск' THEN 2
        WHEN 'вск 14 мск' THEN 3
        WHEN 'вск 21 мск' THEN 4
        ELSE 99
    END;

-- Диагностика (если снова пусто — запустите отдельно):
-- SELECT COUNT(*) AS keys_in_scope FROM ref_keys
-- WHERE name ILIKE '%Вар%' OR ref_key ~ '^2026051[67]_';
-- SELECT ref_key, name FROM ref_keys WHERE name ILIKE '%Вар%' ORDER BY name;
