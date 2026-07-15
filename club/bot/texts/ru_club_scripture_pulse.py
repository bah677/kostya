from __future__ import annotations

"""
Пользовательские тексты (RU) для `ClubScripturePulseFeature`.
"""

from bot.texts.ru_targets import where_digest_topic, where_dm


def scripture_pulse_test_usage_text() -> str:
    return (
        "Укажите, куда отправить цитату:\n\n"
        "<code>/scripture_pulse_test личка</code> — вам в личку\n"
        "<code>/scripture_pulse_test группа</code> — в топик дайджеста клуба\n\n"
        "Опционально: час слота (<code>12</code>), "
        "<code>nopersist</code> — не сдвигать метку «последний запуск»\n"
        "Пример: <code>/scripture_pulse_test группа 12</code>"
    )


def scripture_pulse_test_build_text(*, slot_hour: int, where: str) -> str:
    return f"⏳ Подбираю цитату (слот {slot_hour}:00 МСК) → {where}…"


def scripture_pulse_skipped_text(*, skip_reason: str, message_count: int, since_s: str = "") -> str:
    suffix = f"\nпериод с: {since_s} МСК" if since_s else ""
    return f"Пропуск: {skip_reason}\nсообщений: {message_count}{suffix}"


def scripture_pulse_sent_text(*, where: str, message_count: int) -> str:
    return (
        f"✅ Цитата отправлена {where}.\n"
        f"Сообщений в периоде: {message_count}"
    )


def scripture_pulse_failed_text(*, where: str) -> str:
    return f"❌ Не удалось отправить {where}"


def scripture_pulse_skip_too_few_messages(
    *, message_count: int, min_messages: int
) -> str:
    return f"мало сообщений ({message_count} < {min_messages})"


def scripture_pulse_skip_llm_empty() -> str:
    return "LLM не вернул цитату"


def scripture_pulse_where(*, target: str) -> str:
    return where_dm() if target == "dm" else where_digest_topic()

