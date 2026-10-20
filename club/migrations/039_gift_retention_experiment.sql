-- Тестовые когорты удержания подарочной волны gift-2026-09.
--
-- Четыре варианта по 10 человек + контроль. Раскладка делается один раз
-- скриптом scripts/assign_gift_retention_cohorts.py и дальше не меняется:
-- вариант пользователя должен быть неизменным до конца замера, иначе
-- сравнивать будет нечего.
CREATE TABLE IF NOT EXISTS gift_retention_experiment (
    user_id      BIGINT      PRIMARY KEY,
    campaign     TEXT        NOT NULL,
    variant      TEXT        NOT NULL,
    -- Страта, из которой человек взят: активность + волна истечения.
    -- Хранится для аудита: по ней видно, что когорты сопоставимы.
    stratum      TEXT        NOT NULL DEFAULT '',
    expires_on   DATE,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assign_seed  TEXT        NOT NULL DEFAULT '',
    CONSTRAINT gift_retention_variant_chk CHECK (
        variant IN (
            'control',
            'p1_discount',
            'p2_sell5d',
            'p3_group_close',
            'p4_step299'
        )
    )
);

CREATE INDEX IF NOT EXISTS gift_retention_experiment_campaign_variant
    ON gift_retention_experiment (campaign, variant);
