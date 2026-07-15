"""Тесты документации возможностей бота для member-агента."""

from bot.services.bot_capabilities_knowledge import load_member_bot_capabilities
from bot.texts.prompts.agents_club_member import build_club_member_system_prompt


def test_load_member_bot_capabilities_contains_angel_pool():
    text = load_member_bot_capabilities()
    assert "Стать ангелом" in text
    assert "7 000" in text or "7000" in text
    assert "Доска добрых дел" in text


def test_member_prompt_includes_bot_capabilities_block():
    prompt = build_club_member_system_prompt(
        "клуб",
        bot_capabilities="тест: ангельский взнос",
    )
    assert "ВОЗМОЖНОСТИ БОТА" in prompt
    assert "ангельский взнос" in prompt
    assert "тест: ангельский взнос" in prompt
