"""Тесты админ-панели /adm."""

from bot.services.admin_panel import (
    build_admin_console_help_html,
    build_admin_panel_group,
    build_admin_panel_home,
)


def test_admin_help_lists_key_commands():
    body = build_admin_console_help_html("admin", report_hint="")
    assert "/ref_key" in body
    assert "/schedule" in body
    assert "/new_promo" in body
    assert "/digest_test" in body
    assert "/outreach_pilot_refresh" in body
    assert "/outreach_dm_test" in body
    assert "/admins" not in body


def test_superadmin_sees_admin_management():
    body = build_admin_console_help_html("superadmin", report_hint="")
    assert "/admins" in body
    assert "/admin_add" in body


def test_admin_panel_home_has_group_buttons():
    text, kb = build_admin_panel_home("admin")
    assert "Админ-панель" in text
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c and c.startswith("apnl:g:") for c in flat)
    assert "apnl:g:users" in flat
    assert not any(c == "apnl:g:access" for c in flat)


def test_admin_panel_users_group():
    text, _ = build_admin_panel_group("admin", "users")
    assert "/start" in text
    assert "/payment" in text
    assert "/subs" in text


def test_admin_panel_group_funnels():
    text, kb = build_admin_panel_group("admin", "funnels")
    assert "/ref_key" in text
    assert any(b.callback_data == "apnl:h" for row in kb.inline_keyboard for b in row)


def test_superadmin_panel_has_access_group():
    _, kb = build_admin_panel_home("superadmin")
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "apnl:g:access" in flat
    assert "apnl:g:users" in flat
