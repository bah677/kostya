"""Тесты текста разового анонса «Стать ангелом»."""

from bot.services.wish_board_deeplink import START_ANGEL, build_angel_pool_deeplink
from bot.texts import ru_angel_pool as ap_txt


def test_announcement_uses_assistant_not_bot():
    html = ap_txt.build_group_announcement_html(test=False)
    assert "ассистент клуба" in html
    assert "бот делит" not in html.lower()
    assert "экран бота" not in html.lower()


def test_announcement_test_prefix():
    html = ap_txt.build_group_announcement_html(test=True)
    assert "[ТЕСТ]" in html
    assert "ассистент клуба" in html


def test_announcement_has_scripture():
    html = ap_txt.build_group_announcement_html(test=False)
    assert "2 Кор. 9:7" in html
    assert "blockquote" in html


def test_deeplink_angel():
    url = build_angel_pool_deeplink("ClubBot")
    assert url == f"https://t.me/ClubBot?start={START_ANGEL}"
