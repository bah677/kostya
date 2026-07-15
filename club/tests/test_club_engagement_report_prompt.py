"""Тесты промпта ежедневного отчёта по вовлечённости."""

from bot.texts.prompts.club_engagement_report import (
    ENGAGEMENT_REPORT_INSIGHTS_SYSTEM,
    build_engagement_report_llm_user_message,
    build_engagement_runtime_context,
)


def test_engagement_system_prompt_forbids_fake_mechanics():
    text = ENGAGEMENT_REPORT_INSIGHTS_SYSTEM.lower()
    assert "любящие бога" in text
    assert "+3 дня" in text
    assert "скидка 20%" in text
    assert "не курс" in text or "клуб не курс" in text
    assert "молчали везде" in text


def test_engagement_system_prompt_requires_russian():
    assert "engagement" not in ENGAGEMENT_REPORT_INSIGHTS_SYSTEM.lower()
    assert "outreach" not in ENGAGEMENT_REPORT_INSIGHTS_SYSTEM.lower()


def test_build_engagement_report_llm_user_message_sections():
    msg = build_engagement_report_llm_user_message(
        report_date_str="28.06.2026",
        stats_blob="licenses=180; silent=145",
        report_excerpt="1. Ilona — личка 0, группа 23",
        runtime_context="Проактивные рассылки: включены",
    )
    assert "28.06.2026" in msg
    assert "=== Метрики ===" in msg
    assert "=== Что сейчас включено в боте ===" in msg
    assert "=== Фрагмент отчёта" in msg
    assert "licenses=180" in msg


def test_build_engagement_runtime_context_mentions_key_flags():
    ctx = build_engagement_runtime_context()
    assert "Проактивные рассылки" in ctx
    assert "Доска добрых дел" in ctx
    assert "Дайджест в группу" in ctx
