"""Тесты отчёта конверсии ТД."""

from bot.services.td_conversion_report import (
    TdConversionReport,
    format_td_conversion_html,
)


def test_format_td_conversion_html():
    report = TdConversionReport(
        days=30,
        buyers=100,
        total_rub=150_000.0,
        active_td=20,
        expired_no_renew=50,
        renewed=30,
    )
    html = format_td_conversion_html(report)
    assert "100 чел." in html
    assert "150 000 ₽" in html
    assert "30 чел." in html
    assert "30.0%" in html
    assert "Конверсия ТД → base" in html


def test_conversion_pct_zero_buyers():
    report = TdConversionReport(
        days=7,
        buyers=0,
        total_rub=0,
        active_td=0,
        expired_no_renew=0,
        renewed=0,
    )
    assert report.conversion_pct == 0.0
    html = format_td_conversion_html(report)
    assert "0 чел." in html
