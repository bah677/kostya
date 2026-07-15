"""Тесты разбора аргументов /report."""

from bot.services.report_cli import parse_report_command_args


def test_report_biblia_only():
    opts = parse_report_command_args("biblia")
    assert opts.biblia_club_only is True
    assert opts.include_v2 is False
    assert opts.include_legacy is False
    assert opts.include_llm is False


def test_report_biblia_club_alias():
    opts = parse_report_command_args("biblia_club")
    assert opts.biblia_club_only is True


def test_report_default_full():
    opts = parse_report_command_args(None)
    assert opts.biblia_club_only is False
    assert opts.include_v2 is True
