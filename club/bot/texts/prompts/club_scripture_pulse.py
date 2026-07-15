from __future__ import annotations

"""
LLM-промпты для «пульса Писания» (scripture_pulse).
"""

from bot.texts.prompts.scripture_quote_rules import SCRIPTURE_FULL_VERSE_RULE

SCRIPTURE_QUOTE_SYSTEM_PROMPT = (
    "Подбери одну цитату из Священного Писания (русский текст), "
    "уместную к переписке участников христианского клуба. "
    f"{SCRIPTURE_FULL_VERSE_RULE}\n\n"
    "РАЗНООБРАЗИЕ (обязательно): каждый раз — другой стих. "
    "Не повторяй места из блока «Уже отправляли в топик» в user-сообщении: "
    "другая книга, глава и стих; другой текст, не перефразируй недавние цитаты.\n\n"
    "Ответ — ТОЛЬКО один фрагмент Telegram HTML:\n"
    "<blockquote>полный текст стиха\n\n<i>(книга глава:стих)</i></blockquote>\n\n"
    "Без вступлений, пояснений и других тегов. "
    "Не используй «сегодня» — это цитата к диалогу за последние часы."
)


def format_recent_pulse_quotes_user_block(recent_refs: list[str]) -> str:
    """Блок для user prompt: недавние цитаты, которые нельзя повторять."""
    if not recent_refs:
        return ""
    lines = "\n".join(f"- {ref}" for ref in recent_refs[-30:])
    return (
        "\n\n<<<УЖЕ ОТПРАВЛЯЛИ В ТОПИК (не повторять эти места и этот текст)>>>"
        f"\n{lines}\n<<<КОНЕЦ СПИСКА>>>"
    )


RETRY_USER_SUFFIX_TOO_STRICT = (
    "\n\n⚠️ Нужен только <blockquote>…</blockquote> с полным текстом стиха и "
    "<i>(книга глава:стих)</i> внутри, до 900 символов. Стих не сокращай."
)

RETRY_USER_SUFFIX_DUPLICATE = (
    "\n\n⚠️ Эта цитата (место или текст) уже была в списке недавних. "
    "Выбери другой стих — другая книга/глава/стих, другой текст."
)

