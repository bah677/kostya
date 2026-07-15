"""Тесты контекста доступа member-агента."""

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.services.member_license_context import (
    access_reply_is_too_vague,
    build_access_status_reply_html,
    build_member_license_facts_addon,
    build_natural_language_turn_addon,
    looks_like_access_question,
    looks_like_short_ack,
)
from storage.license_types import LICENSE_TYPE_ADMIN_SUBSCRIPTION

MSK = ZoneInfo("Europe/Moscow")


def test_access_question_detection():
    assert looks_like_access_question("что у меня с доступом к клубу?")
    assert looks_like_access_question("пускает в группу?")
    assert not looks_like_access_question("расскажи про молитву утром")


def test_short_ack_detection():
    assert looks_like_short_ack("Конесно, расскажи")
    assert looks_like_short_ack("да")
    assert not looks_like_short_ack("что у меня с доступом?")


def test_license_facts_admin_subscription():
    text = build_member_license_facts_addon(
        {
            "license_type": LICENSE_TYPE_ADMIN_SUBSCRIPTION,
            "expires_at": datetime(2100, 1, 30, 23, 59, 59, tzinfo=MSK),
        }
    )
    assert "активное участие" in text
    assert "админская подписка" in text
    assert "без ограничения" in text


def test_natural_language_direct_question():
    addon = build_natural_language_turn_addon("что у меня с доступом?")
    assert "ФАКТЫ О ДОСТУПЕ" in addon or "доступ" in addon.lower()


def test_vague_access_reply_detected():
    assert access_reply_is_too_vague(
        "Привет! У вас всё в порядке, доступ есть.",
        has_active_license=True,
    )
    assert not access_reply_is_too_vague(
        "У вас админская подписка, действует до 30.01.2100.",
        has_active_license=True,
    )


def test_access_status_reply_html():
    html = build_access_status_reply_html(
        {
            "license_type": LICENSE_TYPE_ADMIN_SUBSCRIPTION,
            "expires_at": datetime(2100, 1, 30, 23, 59, 59, tzinfo=MSK),
        }
    )
    assert "активное участие" in html
    assert "/club" in html


def test_club_link_problem_detection():
    from bot.services.member_license_context import looks_like_club_link_problem

    assert looks_like_club_link_problem("Все равно не работает ссылка в клуб")
    assert looks_like_club_link_problem("Не получила ссылку на вступление")
    assert not looks_like_club_link_problem("расскажи про молитву утром")
