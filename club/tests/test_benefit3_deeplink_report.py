"""Тесты отчёта deep link benefit3."""

from bot.services.benefit3_deeplink_report import (
    Benefit3DeeplinkReport,
    Benefit3PeriodReport,
    Benefit3TariffRow,
    format_benefit3_deeplink_block,
)


def test_format_benefit3_deeplink_block():
    report = Benefit3DeeplinkReport(
        yesterday=Benefit3PeriodReport(
            period_key="yesterday",
            period_label="вчера",
            touch_events=5,
            unique_users=4,
            first_time=3,
            repeat_users=1,
            first_buyers=1,
            first_rub=990.0,
            repeat_buyers=1,
            repeat_rub=4990.0,
            tariffs_first=[
                Benefit3TariffRow("Тест неделя", 1, 990.0),
            ],
            tariffs_repeat=[
                Benefit3TariffRow("Базовый", 1, 4990.0),
            ],
        ),
        days_30=Benefit3PeriodReport(
            period_key="30d",
            period_label="30 дней",
            unique_users=20,
            first_time=15,
            repeat_users=5,
        ),
    )
    html = format_benefit3_deeplink_block(report)
    assert "Deep link benefit3" in html
    assert "вчера" in html
    assert "30 дней" in html
    assert "4 чел. (5 запусков)" in html
    assert "Впервые: 3" in html
    assert "Тест неделя" in html
    assert "990 ₽" in html


def test_format_benefit3_empty_returns_empty():
    assert format_benefit3_deeplink_block(None) == ""
