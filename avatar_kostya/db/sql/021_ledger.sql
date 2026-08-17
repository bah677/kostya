-- Журнал депозита и расходов. Сейчас одна валюта USDT;
-- схема готова к счетам по валютам и пересчёту в базовую.

CREATE TABLE IF NOT EXISTS ledger_currencies (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    is_base         BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_currencies_one_base
    ON ledger_currencies ((is_base))
    WHERE is_base;

CREATE TABLE IF NOT EXISTS ledger_accounts (
    id              SERIAL PRIMARY KEY,
    currency        TEXT NOT NULL REFERENCES ledger_currencies(code),
    name            TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    -- Остаток в валюте счёта (нетто-зачисления минус списания).
    balance         NUMERIC(20, 8) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (currency, name)
);

CREATE TABLE IF NOT EXISTS ledger_categories (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_categories_name_ci
    ON ledger_categories (lower(btrim(name)));

CREATE TABLE IF NOT EXISTS ledger_entries (
    id                      BIGSERIAL PRIMARY KEY,
    account_id              INTEGER NOT NULL REFERENCES ledger_accounts(id),
    currency                TEXT NOT NULL REFERENCES ledger_currencies(code),
    kind                    TEXT NOT NULL CHECK (kind IN ('deposit', 'expense')),
    category_id             INTEGER REFERENCES ledger_categories(id),
    category_name           TEXT,
    amount_gross            NUMERIC(20, 8) NOT NULL CHECK (amount_gross > 0),
    amount_fee              NUMERIC(20, 8) NOT NULL DEFAULT 0 CHECK (amount_fee >= 0),
    -- Нетто без комиссии: брутто − комиссия (для отчётов).
    amount_net              NUMERIC(20, 8) NOT NULL,
    -- Влияние на остаток счёта: депозит +(брутто−комиссия), расход −(брутто+комиссия).
    balance_delta           NUMERIC(20, 8) NOT NULL,
    balance_after           NUMERIC(20, 8) NOT NULL,
    -- Задел под мультивалютность: сейчас копия 1:1 в базовую USDT.
    base_currency           TEXT REFERENCES ledger_currencies(code),
    fx_rate                 NUMERIC(20, 8),
    fx_source               TEXT,
    amount_gross_base       NUMERIC(20, 8),
    amount_fee_base         NUMERIC(20, 8),
    amount_net_base         NUMERIC(20, 8),
    balance_delta_base      NUMERIC(20, 8),
    note                    TEXT,
    created_by              BIGINT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_account_created
    ON ledger_entries (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_kind_created
    ON ledger_entries (kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_category
    ON ledger_entries (category_id)
    WHERE category_id IS NOT NULL;

INSERT INTO ledger_currencies (code, name, is_base, sort_order)
VALUES ('USDT', 'Tether USD', TRUE, 1)
ON CONFLICT (code) DO NOTHING;

INSERT INTO ledger_accounts (currency, name)
VALUES ('USDT', 'Основной USDT')
ON CONFLICT (currency, name) DO NOTHING;

INSERT INTO ledger_categories (name)
VALUES ('Хостинг'), ('Реклама'), ('Подписки / API'), ('Прочее')
ON CONFLICT DO NOTHING;

COMMENT ON TABLE ledger_entries IS
    'Неизменяемый журнал: брутто, комиссия и нетто всегда сохраняются.';
COMMENT ON COLUMN ledger_entries.amount_net IS
    'Брутто минус комиссия. Для расходов в отчёте это «нетто», списание с депозита — −balance_delta.';
COMMENT ON COLUMN ledger_entries.balance_delta IS
    'Депозит: +(gross-fee). Расход: −(gross+fee).';
