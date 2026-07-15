"""Блоки промпта member-агента при подмешивании RAG."""

from bot.texts.prompts.rag_augmentation import (
    GOLDEN_SECTION_HEADER,
    RAG_SECTION_EMPTY,
    RAG_SECTION_HEADER,
)

MEMBER_RAG_RULES = (
    "Правила для участника клуба:",
    "- Опирайся на фрагменты, описание клуба и блок «ВОЗМОЖНОСТИ БОТА»; не выдумывай факты, даты и названия эфиров.",
    "- Вопросы «как сделать в боте» — сначала блок возможностей бота, потом фрагменты RAG.",
    "- Если рекомендуешь конкретный материал — дай ссылку из поля «ссылка» фрагмента (t.me).",
    "- Не упоминай RAG, Chroma, индексацию.",
    "- «Золотой фонд» — ориентир по тону, не копируй пост целиком.",
    "- Не продавай подписку — человек уже в клубе.",
)


def augment_member_system_prompt_with_rag(
    base_system: str,
    *,
    retrieved_context: str,
    golden_block: str,
) -> str:
    rc = (retrieved_context or "").strip()
    gb = (golden_block or "").strip()
    if not rc and not gb:
        return base_system

    lines = [
        base_system,
        "",
        RAG_SECTION_HEADER,
        rc or RAG_SECTION_EMPTY,
    ]
    if gb:
        lines.extend(["", GOLDEN_SECTION_HEADER, gb])
    lines.extend(["", *MEMBER_RAG_RULES])
    return "\n".join(lines)
