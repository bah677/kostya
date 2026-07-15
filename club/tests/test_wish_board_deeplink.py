"""Тесты deep link доски добрых дел."""

from bot.services.wish_board_deeplink import (
    START_ANGEL,
    build_angel_pool_deeplink,
    build_wish_board_deeplink,
    parse_wish_board_start_param,
)


def test_parse_angel():
    t = parse_wish_board_start_param("ddd_angel")
    assert t is not None
    assert t.kind == "angel"


def test_parse_angel_aliases():
    for p in ("ddd_angel", "angel", "DDD_ANGEL"):
        t = parse_wish_board_start_param(p)
        assert t is not None
        assert t.kind == "angel"


def test_build_angel_deeplink():
    url = build_angel_pool_deeplink("MyClubBot")
    assert url == f"https://t.me/MyClubBot?start={START_ANGEL}"


def test_build_wish_board_to_angel():
    url = build_wish_board_deeplink("bot", to_angel=True)
    assert url.endswith(f"start={START_ANGEL}")
