"""Исключение тестировщиков из аналитики и отчётов."""

from __future__ import annotations

import os
from typing import List, Sequence, Tuple

from config import _parse_subscription_chain_test_user_ids


def report_exclude_user_ids() -> Tuple[int, ...]:
    """ID из ``SUBSCRIPTION_CHAIN_TEST_USER_IDS`` (всегда из .env, не только при test chain)."""
    raw = (os.getenv("SUBSCRIPTION_CHAIN_TEST_USER_IDS") or "").strip()
    return _parse_subscription_chain_test_user_ids(raw)


def sql_exclude_users(
    column: str,
    *,
    start_param: int = 1,
    extra_ids: Sequence[int] | None = None,
) -> Tuple[str, List[int]]:
    """Фрагмент ``AND col NOT IN (...)`` и список id для bind."""
    ids = list(report_exclude_user_ids())
    if extra_ids:
        ids.extend(int(x) for x in extra_ids)
    ids = sorted(set(ids))
    if not ids:
        return "", []
    ph = ", ".join(f"${start_param + i}" for i in range(len(ids)))
    return f" AND {column} NOT IN ({ph})", ids
