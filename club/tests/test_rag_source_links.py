"""Тесты классификации ссылок RAG и форматирования промпта."""

from __future__ import annotations

from openai_client.member_agent_verifier import (
    extract_allowed_links_from_context,
    extract_links_from_draft,
)
from openai_client.member_agent_verifier import _heuristic_block
from rag.source_links import (
    apply_classified_link_metadata,
    classify_source_link_visibility,
    is_public_member_link,
)
from rag.types import format_retrieval_line


def test_club_group_link_is_public():
    url = "https://t.me/c/3882558802/42"
    assert classify_source_link_visibility(url) == "public"
    assert is_public_member_link(url)


def test_training_group_link_is_private():
    url = "https://t.me/c/3756916561/127"
    assert classify_source_link_visibility(url) == "private"
    assert not is_public_member_link(url)


def test_migrate_legacy_to_private():
    meta, changed = apply_classified_link_metadata(
        {"group_message_link": "https://t.me/c/3756916561/127"}
    )
    assert changed
    assert meta.get("private_source_link", "").startswith("https://t.me/c/3756916561/")
    assert not (meta.get("group_message_link") or "").strip()


def test_migrate_legacy_club_to_public():
    meta, changed = apply_classified_link_metadata(
        {"group_message_link": "https://t.me/c/3882558802/99"}
    )
    assert changed
    assert meta.get("public_source_link", "").endswith("/99")


def test_format_retrieval_hides_private_link():
    line = format_retrieval_line(
        {
            "source": "test",
            "private_source_link": "https://t.me/c/3756916561/1",
            "public_source_link": "https://t.me/c/3882558802/2",
        },
        "текст",
    )
    assert "3756916561" not in line
    assert "3882558802" in line
    assert "публичная ссылка" in line


def test_allowed_links_only_public_from_context():
    ctx = (
        "[источник: x | публичная ссылка: https://t.me/c/3882558802/1]\nтекст\n"
        "[источник: y | private_source_link: https://t.me/c/3756916561/2]\nтекст2"
    )
    allowed = extract_allowed_links_from_context(ctx)
    assert allowed == ["https://t.me/c/3882558802/1"]


def test_heuristic_blocks_private_tme_even_if_invented():
    draft = '<a href="https://t.me/c/3756916561/5">смотри</a>'
    result = _heuristic_block(draft, allowed_links=[])
    assert result is not None
    assert not result.ok


def test_heuristic_allows_public_link_from_allowlist():
    url = "https://t.me/c/3882558802/10"
    draft = f'<a href="{url}">эфир</a>'
    result = _heuristic_block(draft, allowed_links=[url])
    assert result is None
