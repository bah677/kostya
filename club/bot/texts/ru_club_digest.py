from __future__ import annotations

"""
Пользовательские тексты (RU) для `ClubDigestFeature`.
"""

from datetime import date
from typing import Optional


def digest_test_usage_text() -> str:
    return (
        "Укажите, куда отправить дайджест (полный текст, как по расписанию):\n\n"
        "<code>/digest_test личка</code> — вам в личку\n"
        "<code>/digest_test группа</code> — в топик дайджеста клуба (CLUB_DIGEST_TOPIC_ID)\n\n"
        "Алиас: <code>/club_digest_test</code>"
    )


def digest_test_build_text() -> str:
    return "⏳ Собираю дайджест…"


def digest_test_skipped_text(
    *,
    skip_reason: str,
    message_count: int,
    participant_count: int,
) -> str:
    return (
        f"Дайджест не собран: {skip_reason}\n"
        f"сообщений: {message_count}, участников: {participant_count}"
    )


def digest_test_sent_text(*, where: str, message_count: int, participant_count: int) -> str:
    return (
        f"✅ Дайджест отправлен {where}.\n"
        f"Сообщений: {message_count}, участников: {participant_count}"
    )


def digest_test_failed_text(*, where: str) -> str:
    return f"❌ Не удалось отправить дайджест {where}"


def digest_skip_too_few_messages(*, message_count: int, min_messages: int) -> str:
    return f"мало сообщений ({message_count} < {min_messages})"


def digest_skip_too_few_participants(
    *, participant_count: int, min_participants: int
) -> str:
    return f"мало участников ({participant_count} < {min_participants})"


def digest_skip_llm_empty() -> str:
    return "LLM не вернул текст"


def digest_title_line(*, report_date: date) -> str:
    return f"<b>☀️ Дайджест клуба</b> · {report_date.strftime('%d.%m.%Y')}"


def digest_admin_published_html(*, message_count: int, participant_count: int) -> str:
    return (
        f"<b>☀️ Дайджест клуба опубликован</b>\n"
        f"Сообщений: {message_count}, участников: {participant_count}"
    )


def digest_admin_skipped_html(
    *,
    skip_reason: str,
    message_count: int,
    participant_count: int,
) -> str:
    return (
        f"<b>☀️ Дайджест клуба пропущен</b>\n"
        f"{skip_reason}\n"
        f"Сообщений: {message_count}, участников: {participant_count}"
    )

