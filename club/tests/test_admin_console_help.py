"""Тесты справки /adm."""

from bot.services.bot_help import build_admin_console_help_html


def test_admin_help_lists_key_commands():
    body = build_admin_console_help_html("admin", report_hint="")
    assert "/ref_key" in body
    assert "/schedule" in body
    assert "/new_promo" in body
    assert "/digest_test" in body
    assert "/outreach_pilot_refresh" in body
    assert "/outreach_dm_test" in body
    assert "Новости" in body
    assert "/admins" not in body


def test_superadmin_sees_admin_management():
    body = build_admin_console_help_html("superadmin", report_hint="")
    assert "/admins" in body
    assert "/admin_add" in body
