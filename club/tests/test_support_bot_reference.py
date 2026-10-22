"""Справка по боту для черновиков поддержки и ловля выдуманных путей.

Повод: в системном промпте лежал список путей, написанный руками, и он
разошёлся с ботом — в промпте «/menu → Оплатить подписку», в меню
«💳 Оплата и тарифы». Модель повторяла шпаргалку, люди искали несуществующий
пункт. Теперь справка собирается из тех же констант, что рисуют меню.
"""

from __future__ import annotations

from bot.services.support_bot_reference import (
    build_bot_reference,
    find_invented_menu_paths,
    known_menu_labels,
)
from bot.texts import ru_user_menu as menu_txt


def test_reference_uses_real_labels():
    ref = build_bot_reference()
    assert menu_txt.BTN_PAYMENT in ref
    assert menu_txt.BTN_MEMBER_GIFT in ref
    assert menu_txt.BTN_WISH_BOARD in ref


def test_reference_has_no_stale_labels():
    """Старые выдуманные подписи не должны появиться снова."""
    ref = build_bot_reference()
    assert "Оплатить подписку" not in ref
    # «Подарить продление» — префикс реальной кнопки, проверяем точную старую форму
    assert "→ Подарить продление\n" not in ref


def test_catches_invented_path():
    assert find_invented_menu_paths("откройте /menu → Оплатить подписку")
    assert find_invented_menu_paths(
        "нажмите /menu и выберите раздел с архивом эфиров"
    )


def test_accepts_real_paths():
    assert not find_invented_menu_paths(f"зайдите в /menu → «{menu_txt.BTN_PAYMENT}»")
    assert not find_invented_menu_paths(f"/menu → {menu_txt.BTN_WISH_BOARD}")


def test_accepts_near_miss_of_real_label():
    """Модель часто пишет подпись не дословно — это не повод браковать."""
    assert not find_invented_menu_paths("/menu → «Подарить продление»")
    assert not find_invented_menu_paths("/menu → Доска добрых дел")


def test_no_false_positive_without_menu_mention():
    assert not find_invented_menu_paths("проверьте срок через /subs")
    assert not find_invented_menu_paths("напишите нам через /support")


def test_known_labels_not_empty():
    labels = known_menu_labels()
    assert len(labels) >= 5
    assert all(isinstance(x, str) and x.strip() for x in labels)
