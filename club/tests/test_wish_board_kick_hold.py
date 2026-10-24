"""Отсрочка исключения из группы, пока по человеку есть живая просьба.

Повод: по просьбе №58 даритель держал обещание помочь три дня, а бот тем
временем закрыл просителю вход в группу. Человека выставили, пока он ждал.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from bot.features.subscription_reminder import SubscriptionReminderFeature

TODAY = date(2026, 10, 24)


def _feature(wish, *, claimed=True):
    storage = AsyncMock()
    storage.wish_active_for_requester = AsyncMock(return_value=wish)
    storage.try_claim_subscription_outreach = AsyncMock(return_value=claimed)
    f = SubscriptionReminderFeature(storage, AsyncMock())
    f.feature_manager = MagicMock()
    f._send_html = AsyncMock(return_value=True)
    return f, storage


def _lic(days_overdue: int):
    exp = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
    return {
        "user_id": 42,
        "expires_at": exp.replace(day=24 - days_overdue) if days_overdue else exp,
    }


def _wish(**kw):
    base = {
        "id": 58,
        "status": "taken",
        "donor_user_id": 777,
        "button_title": "Продлить участие в клубе на месяц",
    }
    base.update(kw)
    return base


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_hold_when_request_is_taken():
    f, _ = _feature(_wish())
    assert _run(f._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is True


def test_hold_when_request_is_merely_open():
    """Открытая просьба тоже держит — человек ещё может получить помощь."""
    f, _ = _feature(_wish(status="open", donor_user_id=None))
    assert _run(f._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is True


def test_no_hold_without_request():
    f, _ = _feature(None)
    assert _run(f._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is False


def test_no_hold_on_broken_wish_data():
    """Кривые данные не должны оставлять человека в группе навсегда."""
    f, _ = _feature({"id": None, "status": "taken"})
    assert _run(f._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is False
    f2, _ = _feature({"id": 58, "status": "completed"})
    assert _run(f2._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is False


def test_grace_window_is_limited():
    """Через grace_days отсрочка кончается — иначе доступ был бы бесконечным."""
    f, _ = _feature(_wish())
    assert _run(f._hold_removal_for_wish(_lic(10), today_msk=TODAY)) is False


def test_donor_and_requester_notified_once():
    f, storage = _feature(_wish())
    assert _run(f._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is True
    # оба письма: просителю и дарителю
    assert f._send_html.await_count == 2
    uids = [c.args[0] for c in f._send_html.await_args_list]
    assert 42 in uids and 777 in uids
    # слот именной — одно письмо на просьбу, а не каждый день отсрочки
    slug = storage.try_claim_subscription_outreach.await_args.args[1]
    assert slug == "wish_hold_58"


def test_no_second_notification_when_slot_taken():
    f, _ = _feature(_wish(), claimed=False)
    assert _run(f._hold_removal_for_wish(_lic(1), today_msk=TODAY)) is True
    assert f._send_html.await_count == 0


def test_open_request_notifies_requester_only():
    f, _ = _feature(_wish(status="open", donor_user_id=None))
    assert _run(f._hold_removal_for_wish(_lic(0), today_msk=TODAY)) is True
    assert f._send_html.await_count == 1
