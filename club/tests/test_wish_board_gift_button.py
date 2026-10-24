"""Кнопка «🎁 Подарить продление» на доске добрых дел.

Она не работала ни разу. Колбэк передавал в start_for_recipient
`callback.message` — сообщение, отправленное БОТОМ, — а дарителя брали из
`message.from_user`, то есть получали id бота. Сравнение с
`wish.donor_user_id` не совпадало никогда, и каждый даритель видел
«❌ Эту просьбу уже нельзя оплатить — возможно, её отменили или взял другой
даритель».

Отсюда и бесконечные «взял → отказался»: человек жал кнопку, упирался в
ошибку и возвращал просьбу в ленту.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from bot.features.member_gift_extension import MemberGiftExtensionFeature

BOT_ID = 8768984272
DONOR_ID = 1241623597
REQUESTER_ID = 963973323
WISH_ID = 52


def _wish(**kw):
    base = {
        "id": WISH_ID,
        "status": "taken",
        "gift_type": "subscription",
        "donor_user_id": DONOR_ID,
        "requester_user_id": REQUESTER_ID,
    }
    base.update(kw)
    return base


def _feature(wish):
    st = AsyncMock()
    st.wish_get = AsyncMock(return_value=wish)
    f = MemberGiftExtensionFeature(st, AsyncMock())
    return f, st


def _run(c):
    return asyncio.run(c)


def test_validation_passes_for_real_donor():
    f, _ = _feature(_wish())
    assert _run(
        f._validate_wish_board_gift(
            wish_id=WISH_ID, donor_id=DONOR_ID, recipient_id=REQUESTER_ID
        )
    ) is True


def test_bot_id_as_donor_is_rejected():
    """Ровно та подстановка, из-за которой кнопка молчала."""
    f, _ = _feature(_wish())
    assert _run(
        f._validate_wish_board_gift(
            wish_id=WISH_ID, donor_id=BOT_ID, recipient_id=REQUESTER_ID
        )
    ) is False


def test_start_for_recipient_prefers_explicit_donor():
    """Явный donor_id важнее from_user: там сидит бот."""
    f, st = _feature(_wish())
    f._prompt_confirm_recipient = AsyncMock()
    st.get_user = AsyncMock(return_value={"first_name": "Ilona"})

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = BOT_ID  # так и приходит callback.message
    state = AsyncMock()

    _run(
        f.start_for_recipient(
            msg, state, REQUESTER_ID, wish_id=WISH_ID, donor_id=DONOR_ID
        )
    )
    # дошли до экрана подтверждения, а не до ошибки
    f._prompt_confirm_recipient.assert_awaited_once()
    saved = state.update_data.await_args.kwargs
    assert saved["gift_recipient_user_id"] == REQUESTER_ID
    assert saved["wish_board_wish_id"] == WISH_ID


def test_start_for_recipient_without_explicit_donor_falls_back():
    """Без явного donor_id берём из сообщения — для обычных Message это верно."""
    f, st = _feature(_wish())
    f._prompt_confirm_recipient = AsyncMock()
    st.get_user = AsyncMock(return_value={"first_name": "Ilona"})

    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = DONOR_ID
    _run(f.start_for_recipient(msg, AsyncMock(), REQUESTER_ID, wish_id=WISH_ID))
    f._prompt_confirm_recipient.assert_awaited_once()


def test_wrong_status_still_rejected():
    """Настоящие причины отказа продолжают работать."""
    for bad in ({"status": "open"}, {"gift_type": "other"},
                {"requester_user_id": 999}):
        f, _ = _feature(_wish(**bad))
        assert _run(
            f._validate_wish_board_gift(
                wish_id=WISH_ID, donor_id=DONOR_ID, recipient_id=REQUESTER_ID
            )
        ) is False
