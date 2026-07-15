"""Тесты свежего инвайта в клуб."""

from bot.texts import ru_club_group as club_txt


def test_fresh_invite_html_mentions_revoke():
    html = club_txt.fresh_invite_html(
        inside_block=club_txt.club_inside_block(),
        invite_footer=club_txt.invite_link_footer(ttl_hours=24),
    )
    assert "отозвали" in html.lower()
    assert "новая ссылка" in html.lower()


def test_fresh_invite_ack_html():
    assert "отдельным сообщением" in club_txt.CLUB_FRESH_INVITE_ACK_HTML.lower()
    assert "старые" in club_txt.CLUB_FRESH_INVITE_ACK_HTML.lower()
