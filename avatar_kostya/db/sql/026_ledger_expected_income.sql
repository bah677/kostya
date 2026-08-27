-- Журнал ожидаемого прихода брутто: 35% донатов Biblia (USD), нарастающий итог по дням МСК.

CREATE TABLE IF NOT EXISTS ledger_expected_income_days (
    day                     DATE PRIMARY KEY,
    donations_count         INTEGER NOT NULL DEFAULT 0,
    donations_rub           NUMERIC(20, 8) NOT NULL DEFAULT 0,
    usd_rub_rate            NUMERIC(20, 8),
    donations_usd           NUMERIC(20, 8) NOT NULL DEFAULT 0,
    share_pct               NUMERIC(10, 6) NOT NULL DEFAULT 0.35,
    day_share_usd           NUMERIC(20, 8) NOT NULL DEFAULT 0,
    cumulative_share_usd    NUMERIC(20, 8) NOT NULL DEFAULT 0,
    source                  TEXT NOT NULL DEFAULT 'biblia_bot',
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ledger_expected_income_days IS
    'Ожидаемый приход брутто (USDT≈USD): 35% суммы донатов Biblia за день, нарастающий итог с 2026-08-16.';
COMMENT ON COLUMN ledger_expected_income_days.day IS
    'Календарный день Europe/Moscow (сутки закрыты в 24:00).';
COMMENT ON COLUMN ledger_expected_income_days.cumulative_share_usd IS
    'Нарастающий итог day_share_usd с даты старта журнала.';
