"""Тесты слияния stated_goals (без перезаписи)."""

from bot.services.member_goals_merge import merge_stated_goals_fragment


def test_empty_fragment_unchanged():
    merged, changed = merge_stated_goals_fragment("цель одна", "")
    assert not changed
    assert merged == "цель одна"


def test_short_fragment_rejected():
    merged, changed = merge_stated_goals_fragment("", "да")
    assert not changed
    assert merged == ""


def test_first_goal_appended():
    merged, changed = merge_stated_goals_fragment("", "хочу больше молитвы в быту")
    assert changed
    assert merged == "хочу больше молитвы в быту"


def test_second_goal_bullet():
    merged, changed = merge_stated_goals_fragment(
        "хочу больше молитвы",
        "важны отношения в семье",
    )
    assert changed
    assert "молитвы" in merged
    assert "отношения" in merged
    assert "\n• " in merged


def test_duplicate_case_insensitive():
    merged, changed = merge_stated_goals_fragment(
        "Хочу больше молитвы",
        "хочу больше молитвы",
    )
    assert not changed
    assert merged == "Хочу больше молитвы"


def test_duplicate_substring():
    merged, changed = merge_stated_goals_fragment(
        "хочу больше молитвы в быту",
        "молитвы",
    )
    assert not changed


def test_existing_bullets_dedup():
    existing = "цель первая\n• цель вторая"
    merged, changed = merge_stated_goals_fragment(existing, "цель вторая")
    assert not changed
    assert merged == existing


def test_max_len_trims():
    existing = "б" * 1900
    frag = "новая цель участника клуба"
    merged, changed = merge_stated_goals_fragment(existing, frag, max_len=2000)
    assert changed
    assert len(merged) <= 2000
    assert "новая цель" in merged
