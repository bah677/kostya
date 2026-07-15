"""Единая точка работы с parse_mode HTML в Telegram Bot API.

Поддерживаемый подмножество тегов см. документацию Telegram для HTML mode.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# В конце ответа менеджера (LLM): показать inline «Вступить в клуб». Строка срезается перед отправкой.
CTA_SUBSCRIBE_MARKER = "<<<CTA_SUBSCRIBE>>>"
# Пробная неделя 299₽ — отдельная кнопка; только при явном возражении «дорого».
CTA_PROMO_WEEK_MARKER = "<<<CTA_PROMO_WEEK>>>"

_ALLOWED_TAG_NAMES = (
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "a",
    "blockquote",
)


def sanitize_telegram_html(text: str | None) -> str:
    """
    Чистка HTML под ограниченный набор Telegram.
    <br> / <br/> → перевод строки; прочие неразрешённые теги удаляются; <a> без href вырезается.
    """
    if not text:
        return ""

    out = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    tags = "|".join(_ALLOWED_TAG_NAMES)
    out = re.sub(rf"<(?!\/?(?:{tags})\b)[^>]*>", "", out, flags=re.IGNORECASE)

    out = re.sub(r"<a\s+(?![^>]*\bhref\s*=)[^>]*>", "", out, flags=re.IGNORECASE)

    return out


# Фрагменты в нижнем регистре (plain): явный призыв к оформлению / входу без маркера от LLM.
_CTA_HEURISTIC_SUBSTRINGS = (
    "хочешь попробовать",
    "попробовать зайти",
    "зайти на месяц",
    "зайти на неделю",
    "оформить подписку",
    "оформить подписк",
    "вступить в клуб",
    "вступить в сообщество",
    "вступить в группу",
    "нажми на кнопку",
    "нажмите на кнопку",
    "выбери тариф",
    "выберите тариф",
    "тариф и оплат",
    "и оплати",
    "продлить подписк",
    "купить подписк",
    "получишь доступ к материал",
    "получите доступ к материал",
)


def _heuristic_subscription_cta(html_text: str) -> bool:
    """Если модель забыла маркер, но текст — явный CTA на подписку/оплату/вход в клуб."""
    t = html_to_plain(html_text).lower()
    t = t.replace("«", '"').replace("»", '"')
    return any(s in t for s in _CTA_HEURISTIC_SUBSTRINGS)


def strip_agent_cta(text: str | None) -> Tuple[str, bool, bool]:
    """Убирает маркеры CTA; (текст, кнопка подписки, кнопка пробной недели)."""
    if not text:
        return "", False, False
    raw = text
    wants_promo = CTA_PROMO_WEEK_MARKER in raw
    wants_sub = CTA_SUBSCRIBE_MARKER in raw
    cleaned = (
        raw.replace(CTA_PROMO_WEEK_MARKER, "")
        .replace(CTA_SUBSCRIBE_MARKER, "")
        .strip()
    )
    if not wants_sub and _heuristic_subscription_cta(cleaned):
        wants_sub = True
    if wants_promo:
        wants_sub = False
    return cleaned, wants_sub, wants_promo


def strip_subscribe_cta(text: str | None) -> Tuple[str, bool]:
    """Убирает маркер оплаты; возвращает (текст для пользователя, показывать ли кнопку)."""
    body, wants_sub, wants_promo = strip_agent_cta(text)
    return body, wants_sub or wants_promo


# Парсер для балансировки при разбиении длинных HTML-сообщений (parse_mode HTML).
_TAG_SPLIT_RE = re.compile(
    r"<(/?)\s*(b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote)\b([^>]*)>",
    re.IGNORECASE | re.DOTALL,
)

# Канонические «слоты» стека: strong/b → bold, em/i → italic, ins → u, strike/del → s
_CANON_TAG = {
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
    "u": "u",
    "ins": "u",
    "s": "s",
    "strike": "s",
    "del": "s",
    "code": "code",
    "pre": "pre",
    "a": "a",
    "blockquote": "blockquote",
}

_OPEN_FOR_CANON = {
    "bold": "<b>",
    "italic": "<i>",
    "u": "<u>",
    "s": "<s>",
    "code": "<code>",
    "pre": "<pre>",
    "blockquote": "<blockquote>",
}

_CLOSE_FOR_CANON = {
    "bold": "</b>",
    "italic": "</i>",
    "u": "</u>",
    "s": "</s>",
    "code": "</code>",
    "pre": "</pre>",
    "a": "</a>",
    "blockquote": "</blockquote>",
}


def _telegram_html_open_stack(fragment: str) -> list[str]:
    """Какие канонические теги остались незакрытыми к концу fragment (порядок открытия)."""
    stack: list[str] = []
    for m in _TAG_SPLIT_RE.finditer(fragment):
        closing = m.group(1) == "/"
        raw = m.group(2).lower()
        slot = _CANON_TAG.get(raw, raw)
        if closing:
            while stack and stack[-1] != slot:
                stack.pop()
            if stack and stack[-1] == slot:
                stack.pop()
        else:
            stack.append(slot)
    return stack


def _balance_telegram_html_chunk_end(chunk: str) -> tuple[str, str]:
    """
    Закрывает незакрытые разрешённые теги в конце chunk; возвращает (chunk_with_closers, prefix_for_next).
    Следующий фрагмент должен начинаться с prefix_for_next + остаток исходного текста.
    """
    open_stack = _telegram_html_open_stack(chunk)
    if not open_stack:
        return chunk, ""
    closer = "".join(_CLOSE_FOR_CANON[c] for c in reversed(open_stack))
    # Ссылку <a href> при разрезе не восстанавливаем (нет href в стеке) — такие разрывы редки при cut по \\n.
    reopener = "".join(
        _OPEN_FOR_CANON[c] for c in open_stack if c in _OPEN_FOR_CANON
    )
    return chunk + closer, reopener


def split_telegram_html_message_chunks(html: str, max_len: int = 3800) -> list[str]:
    """
    Режет HTML для нескольких send_message(..., parse_mode=HTML).

    Нельзя резать посередине тега: Telegram вернёт «Can't find end tag…».
    Режем по переводам строки около max_len; незакрытые теги в конце чанка
    закрываются и заново открываются в начале следующего.
    """
    if not html:
        return [""]
    out: list[str] = []
    rest = html
    guard = 0
    while rest:
        guard += 1
        if guard > 5000:
            raise RuntimeError("split_telegram_html_message_chunks: iteration limit")
        if len(rest) <= max_len:
            out.append(rest)
            break
        cut = rest.rfind("\n", 0, max_len)
        if cut < max_len // 4:
            cut = max_len
        chunk = rest[:cut]
        rest = rest[cut:]
        if rest.startswith("\n"):
            rest = rest[1:]
        fixed, prefix = _balance_telegram_html_chunk_end(chunk)
        out.append(fixed)
        rest = prefix + rest
    return out


def html_to_plain(text: str | None) -> str:
    """Грубое снятие HTML для вспомогательного анализа (LLM), не для показа пользователю."""
    if not text:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def _norm_quick_label(s: str) -> str:
    t = s.strip().lower().replace("ё", "е")
    t = re.sub(r"[.,!?;:…]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    return t


# Подписи «не конкретика» — не показываем как кнопки.
_QUICK_REPLY_GENERIC_EXACT = frozenset(
    {
        "другое",
        "иное",
        "прочее",
        "другой",
        "другая",
        "другие",
        "что-то другое",
        "что то другое",
        "ещё что-то",
        "еще что-то",
        "ещё что",
        "еще что",
        "или другое",
        "и другое",
        "не знаю",
        "свой вариант",
        "своя версия",
        "напишу сам",
        "напишу сама",
        "сам напишу",
        "сама напишу",
        "продолжу в чате",
        "вручную",
        "любое",
        "любой",
        "other",
        "something else",
    }
)


def filter_concrete_quick_reply_choices(choices: List[str]) -> List[str]:
    """Убирает универсальные варианты («другое», «что-то ещё»…); остаются только конкретные метки."""
    out: List[str] = []
    for raw in choices:
        s = str(raw).strip()
        if not s or len(s) > 64:
            continue
        n = _norm_quick_label(s)
        if not n:
            continue
        if n in _QUICK_REPLY_GENERIC_EXACT:
            continue
        # целиком «другое» / «что-то другое»
        if re.fullmatch(
            r"(что[\s\-]*то[\s\-]*)?(другое|иное|прочее)(\.{0,3})?",
            n,
        ):
            continue
        # заканчивается на «… или другое» / «… и другое»
        if re.search(r"(^|\s)(или|и)\s+(другое|иное|прочее)$", n):
            continue
        if re.search(r"что[\s\-]*то[\s\-]*(другое|иное)", n):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= 4:
            break
    return out
