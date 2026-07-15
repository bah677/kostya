"""Тесты сквозного отчёта Библия → Клуб."""

import json

from bot.services.biblia_club_campaign_report import (
    BibliaClubCampaignReport,
    BibliaClubCampaignRow,
    SegmentFunnel,
    extract_start_keys_from_campaign,
    format_biblia_club_campaign_html,
    format_biblia_club_daily_block,
    is_test_campaign_name,
)


def test_extract_start_keys_from_text_and_buttons():
    text = 'Жми <a href="https://t.me/Talk_God_Bot?start=ref_20260428">сюда</a>'
    buttons = json.dumps(
        [
            {
                "url": "https://t.me/Talk_God_Bot?start=benefit3",
                "text": "Получить",
            }
        ]
    )
    keys = extract_start_keys_from_campaign(
        text, buttons, bot_username="Talk_God_Bot"
    )
    assert keys == ["ref_20260428", "benefit3"]


def test_extract_ignores_other_bots():
    text = "https://t.me/Other_Bot?start=foo"
    keys = extract_start_keys_from_campaign(text, None, bot_username="Talk_God_Bot")
    assert keys == []


def test_is_test_campaign_name():
    assert is_test_campaign_name("Тест benefit3")
    assert is_test_campaign_name("smoke TEST run")
    assert is_test_campaign_name("промо testовая")
    assert not is_test_campaign_name("Молитва благодарности в подарок")
    assert not is_test_campaign_name("Молитва благодарности")


def test_format_biblia_club_campaign_html():
    from datetime import datetime, timezone

    report = BibliaClubCampaignReport(
        period_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        period_to=datetime(2026, 7, 5, tzinfo=timezone.utc),
        bot_username="Talk_God_Bot",
        campaigns=(
            BibliaClubCampaignRow(
                campaign_id=140,
                name="Молитва",
                status="completed",
                scheduled_at=datetime(2026, 7, 5, 7, 50, tzinfo=timezone.utc),
                campaign_source="manual",
                audience_size=5000,
                audience_sent=4986,
                sent_count=4986,
                failed_count=10,
                blocked_count=4,
                start_keys=["benefit3"],
                clicks_total=120,
                clients_excluded=5,
                first=SegmentFunnel(clicks=80, ai_dialog=20, ordered=3, paid=2, revenue=1980),
                repeat=SegmentFunnel(clicks=35, ai_dialog=10, ordered=1, paid=1, revenue=990),
            ),
        ),
    )
    html = format_biblia_club_campaign_html(report)
    assert "Библия → Клуб" in html
    assert "benefit3" in html
    assert "Впервые" in html
    assert "Повторно" in html
    assert "уже клиенты 5" in html


def test_format_daily_block_compact():
    from datetime import datetime, timezone

    report = BibliaClubCampaignReport(
        period_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        period_to=datetime(2026, 7, 5, tzinfo=timezone.utc),
        bot_username="Talk_God_Bot",
        campaigns=(
            BibliaClubCampaignRow(
                campaign_id=140,
                name="Молитва благодарности в подарок",
                status="completed",
                scheduled_at=datetime(2026, 7, 5, 7, 50, tzinfo=timezone.utc),
                campaign_source="manual",
                audience_size=5000,
                audience_sent=4986,
                sent_count=4986,
                failed_count=0,
                blocked_count=0,
                start_keys=["benefit3"],
                clicks_total=149,
                first=SegmentFunnel(clicks=81, paid=0, revenue=0),
                repeat=SegmentFunnel(clicks=51, paid=1, revenue=598),
            ),
        ),
    )
    block = format_biblia_club_daily_block(report)
    assert "Библия → Клуб" in block
    assert "доставлено в Библии" in block
    assert "впервые 81" in block
    assert "повторно 51" in block
    assert "Молитва благодарности в подарок" in block
    assert "<code>140</code>" not in block
    assert "/biblia_club" in block
