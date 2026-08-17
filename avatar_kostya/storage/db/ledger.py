"""Mixin: счета, статьи и журнал депозита/расходов."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from bot.services.ledger import DEFAULT_CURRENCY, deposit_amounts, expense_amounts

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
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
    amount_net              NUMERIC(20, 8) NOT NULL,
    balance_delta           NUMERIC(20, 8) NOT NULL,
    balance_after           NUMERIC(20, 8) NOT NULL,
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
"""


class LedgerMixin:
    async def ensure_ledger_schema(self) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(_SCHEMA_SQL)
        except Exception as e:
            logger.warning("ensure_ledger_schema: %s", e)

    async def get_default_ledger_account(
        self, currency: str = DEFAULT_CURRENCY
    ) -> Optional[Dict[str, Any]]:
        cur = (currency or DEFAULT_CURRENCY).upper()
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM ledger_accounts
                     WHERE currency = $1 AND is_active
                     ORDER BY id
                     LIMIT 1
                    """,
                    cur,
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error("get_default_ledger_account: %s", e)
            return None

    async def list_ledger_categories(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, name FROM ledger_categories
                     WHERE is_active
                     ORDER BY name
                    """
                )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_ledger_categories: %s", e)
            return []

    async def upsert_ledger_category(self, name: str) -> Optional[Dict[str, Any]]:
        title = (name or "").strip()
        if not title or len(title) > 80:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, name FROM ledger_categories
                     WHERE lower(btrim(name)) = lower(btrim($1))
                    """,
                    title,
                )
                if row:
                    await conn.execute(
                        "UPDATE ledger_categories SET is_active = TRUE, name = $2 WHERE id = $1",
                        int(row["id"]),
                        title,
                    )
                    return {"id": int(row["id"]), "name": title}
                row = await conn.fetchrow(
                    """
                    INSERT INTO ledger_categories (name)
                    VALUES ($1)
                    RETURNING id, name
                    """,
                    title,
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error("upsert_ledger_category: %s", e)
            return None

    async def add_ledger_entry(
        self,
        *,
        kind: str,
        gross: Decimal,
        fee: Decimal,
        created_by: int,
        category_id: Optional[int] = None,
        category_name: Optional[str] = None,
        note: Optional[str] = None,
        currency: str = DEFAULT_CURRENCY,
    ) -> Optional[Dict[str, Any]]:
        if kind not in ("deposit", "expense"):
            raise ValueError("kind")
        if gross <= 0 or fee < 0:
            raise ValueError("amounts")
        if kind == "deposit":
            if fee > gross:
                raise ValueError("fee > gross")
            net, delta = deposit_amounts(gross, fee)
        else:
            net, delta = expense_amounts(gross, fee)

        cur = (currency or DEFAULT_CURRENCY).upper()
        note_s = (note or "").strip() or None
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    acc = await conn.fetchrow(
                        """
                        SELECT * FROM ledger_accounts
                         WHERE currency = $1 AND is_active
                         ORDER BY id
                         LIMIT 1
                         FOR UPDATE
                        """,
                        cur,
                    )
                    if not acc:
                        raise RuntimeError("no account")
                    new_balance = Decimal(str(acc["balance"])) + delta
                    base = await conn.fetchval(
                        "SELECT code FROM ledger_currencies WHERE is_base LIMIT 1"
                    )
                    base_code = str(base or cur)
                    rate = Decimal("1")
                    row = await conn.fetchrow(
                        """
                        INSERT INTO ledger_entries (
                            account_id, currency, kind, category_id, category_name,
                            amount_gross, amount_fee, amount_net, balance_delta,
                            balance_after, base_currency, fx_rate, fx_source,
                            amount_gross_base, amount_fee_base, amount_net_base,
                            balance_delta_base, note, created_by
                        ) VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, $11, $12, 'identity',
                            $6, $7, $8, $9, $13, $14
                        )
                        RETURNING *
                        """,
                        int(acc["id"]),
                        cur,
                        kind,
                        category_id,
                        (category_name or "").strip() or None,
                        gross,
                        fee,
                        net,
                        delta,
                        new_balance,
                        base_code,
                        rate,
                        note_s,
                        int(created_by),
                    )
                    await conn.execute(
                        "UPDATE ledger_accounts SET balance = $1 WHERE id = $2",
                        new_balance,
                        int(acc["id"]),
                    )
            return dict(row) if row else None
        except Exception as e:
            logger.error("add_ledger_entry: %s", e, exc_info=True)
            return None

    async def list_recent_ledger_entries(
        self, *, limit: int = 8, account_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                if account_id:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM ledger_entries
                         WHERE account_id = $1
                         ORDER BY created_at DESC, id DESC
                         LIMIT $2
                        """,
                        int(account_id),
                        int(limit),
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM ledger_entries
                         ORDER BY created_at DESC, id DESC
                         LIMIT $1
                        """,
                        int(limit),
                    )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_recent_ledger_entries: %s", e)
            return []

    async def ledger_totals(
        self,
        *,
        account_id: int,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Decimal]:
        empty = {
            "deposit_gross": Decimal("0"),
            "deposit_fee": Decimal("0"),
            "deposit_net": Decimal("0"),
            "expense_gross": Decimal("0"),
            "expense_fee": Decimal("0"),
            "expense_net": Decimal("0"),
            "expense_cash": Decimal("0"),
        }
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                      COALESCE(SUM(amount_gross) FILTER (WHERE kind = 'deposit'), 0) AS deposit_gross,
                      COALESCE(SUM(amount_fee) FILTER (WHERE kind = 'deposit'), 0) AS deposit_fee,
                      COALESCE(SUM(amount_net) FILTER (WHERE kind = 'deposit'), 0) AS deposit_net,
                      COALESCE(SUM(amount_gross) FILTER (WHERE kind = 'expense'), 0) AS expense_gross,
                      COALESCE(SUM(amount_fee) FILTER (WHERE kind = 'expense'), 0) AS expense_fee,
                      COALESCE(SUM(amount_net) FILTER (WHERE kind = 'expense'), 0) AS expense_net,
                      COALESCE(SUM(-balance_delta) FILTER (WHERE kind = 'expense'), 0) AS expense_cash
                    FROM ledger_entries
                    WHERE account_id = $1
                      AND ($2::timestamptz IS NULL OR created_at >= $2)
                      AND ($3::timestamptz IS NULL OR created_at < $3)
                    """,
                    int(account_id),
                    since,
                    until,
                )
            if not row:
                return empty
            return {k: Decimal(str(row[k] or 0)) for k in empty}
        except Exception as e:
            logger.error("ledger_totals: %s", e)
            return empty
