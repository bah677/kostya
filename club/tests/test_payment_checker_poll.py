"""Регрессия: poll SQL принимает int; расписание due-check."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bot.payments.payment_checker import payment_due_for_check, poll_interval_for_age


def test_payments_poll_sql_uses_make_interval_not_text_concat():
    src = Path(__file__).resolve().parents[1] / "storage" / "db" / "payments.py"
    text = src.read_text(encoding="utf-8")
    assert "make_interval(days => $1::int)" in text
    assert "($1::text || ' days')::interval" not in text


def test_poll_interval_grows_with_age():
    assert poll_interval_for_age(timedelta(minutes=10)) == timedelta(minutes=1)
    assert poll_interval_for_age(timedelta(hours=2)) == timedelta(minutes=5)
    assert poll_interval_for_age(timedelta(days=20)) == timedelta(hours=24)


def test_payment_due_when_never_checked():
    now = datetime(2026, 8, 15, 12, 0, 0)
    p = {"created_at": now - timedelta(hours=2), "last_checked_at": None}
    assert payment_due_for_check(p, now=now) is True


def test_payment_not_due_within_interval():
    now = datetime(2026, 8, 15, 12, 0, 0)
    created = now - timedelta(hours=2)
    p = {
        "created_at": created,
        "last_checked_at": now - timedelta(minutes=1),
    }
    # возраст ~2ч → интервал 5 мин; checked 1 мин назад → не due
    assert payment_due_for_check(p, now=now) is False


def test_payment_due_after_interval():
    now = datetime(2026, 8, 15, 12, 0, 0)
    created = now - timedelta(hours=2)
    p = {
        "created_at": created,
        "last_checked_at": now - timedelta(minutes=6),
    }
    assert payment_due_for_check(p, now=now) is True


@pytest.mark.asyncio
async def test_make_interval_binds_int_on_live_db():
    """Smoke: asyncpg + make_interval(days => $1::int) с int (не text)."""
    import os

    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg not installed")

    host = os.getenv("DB_HOST")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    port = os.getenv("DB_PORT", "5432")
    if not all([host, name, user, password]):
        pytest.skip("DB_* not configured")

    conn = await asyncpg.connect(
        host=host, port=int(port), user=user, password=password, database=name
    )
    try:
        with pytest.raises(Exception):
            await conn.fetchval(
                "SELECT NOW() - ($1::text || ' days')::interval",
                30,
            )
        val = await conn.fetchval(
            "SELECT NOW() - make_interval(days => $1::int)",
            30,
        )
        assert val is not None
    finally:
        await conn.close()
