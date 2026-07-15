"""Тесты UI доски добрых дел: подписи кнопок и карточка просьбы."""

from __future__ import annotations

import pytest

from bot.services.wish_title_generator import _fallback_title, _normalize_title
from bot.texts import ru_wish_board as wb_txt


def test_wish_button_label_prefers_button_title():
    wish = {
        "id": 42,
        "gift_type": "subscription",
        "description": "На 1 месяц, временные финансовые трудности",
        "button_title": "Продление на месяц",
    }
    assert wb_txt.wish_button_label(wish) == "Продление на месяц"


def test_wish_button_label_falls_back_to_description_snippet():
    desc = "На 1 месяц. Если есть такая возможность буду очень благодарна"
    wish = {"id": 7, "gift_type": "subscription", "description": desc}
    label = wb_txt.wish_button_label(wish)
    assert label.startswith("На 1 месяц")
    assert "#" not in label
    assert "7" not in label


def test_format_wish_card_user_view_blockquote_and_minimal_fields():
    wish = {
        "id": 99,
        "gift_type": "subscription",
        "description": "На 1 месяц. Если есть такая возможность буду очень благодарна",
        "is_anonymous": True,
        "status": "open",
    }
    html = wb_txt.format_wish_card(wish)
    assert "<b>Просьба о помощи</b>" in html
    assert "<b>Тип:</b> Продление участия в клубе" in html
    assert "<b>Автор просьбы:</b> анонимно" in html
    assert "<blockquote>На 1 месяц." in html
    assert "#99" not in html
    assert "Статус" not in html


def test_group_reminder_post_html():
    wish = {
        "id": 3,
        "gift_type": "other",
        "description": "Нужна помощь с оплатой курса",
        "is_anonymous": True,
        "button_title": "Помощь с курсом",
    }
    html = wb_txt.group_reminder_post_html(wish, respond_url="https://t.me/bot?start=wb_3")
    assert "ждёт своего ангела" in html
    assert "Помощь с курсом" in html
    assert "Откликнуться" in html


def test_digest_item_html_no_wish_number():
    wish = {
        "id": 5,
        "gift_type": "subscription",
        "description": "Нужна помощь с продлением",
        "is_anonymous": True,
        "button_title": "Продление участия",
    }
    html = wb_txt.digest_item_html(wish, respond_url="https://t.me/bot?start=wb")
    assert "#5" not in html
    assert "<b>Продление участия</b>" in html
    assert "<blockquote>Нужна помощь с продлением</blockquote>" in html


def test_wish_title_fallback_and_normalize():
    desc = "На 1 месяц. Если есть такая возможность буду очень благодарна"
    title = _fallback_title(desc, "subscription")
    assert title.startswith("На 1 месяц")
    assert _normalize_title('  "#12 Продление на месяц"  ') == "Продление на месяц"


def test_wish_is_digest_postable_only_open_without_notice():
    from bot.services.wish_board_notify import wish_is_digest_postable

    assert wish_is_digest_postable({"status": "open", "digest_notice_message_id": None})
    assert not wish_is_digest_postable({"status": "completed", "digest_notice_message_id": None})
    assert not wish_is_digest_postable({"status": "taken", "digest_notice_message_id": None})
    assert not wish_is_digest_postable({"status": "open", "digest_notice_message_id": 12345})
