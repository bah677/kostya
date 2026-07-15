"""Единая точка работы с parse_mode HTML в Telegram Bot API.

Поддерживаемый подмножество тегов см. документацию Telegram для HTML mode.
"""

from __future__ import annotations

import html as html_module
import re
from typing import Tuple

# В конце ответа менеджера (LLM): показать inline «Вступить в клуб». Строка срезается перед отправкой.
CTA_SUBSCRIBE_MARKER = "<<<CTA_SUBSCRIBE>>>"

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


def _looks_like_telegram_markup(s: str) -> bool:
    """Есть ли в строке типичные HTML-теги под Telegram (после ответа модели)."""
    return bool(
        re.search(
            r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote)\b",
            s,
            re.I,
        )
    )


def normalize_fixed_markdown_phrases(text: str) -> str:
    """
    Точечные замены типичных markdown-конструкций модели на Telegram HTML.

    Общий ``**…**`` иногда ломается на длинных фразах; здесь — явные паттерны.
    """
    if not text:
        return text

    phrase_plain = "Позволь мне предложить тебе поразмышлять над этим:"
    needle = f"**{phrase_plain}**"
    if needle in text:
        text = text.replace(
            needle,
            "<b>" + html_module.escape(phrase_plain) + "</b>",
        )
    return text


def strip_llm_code_fence(text: str) -> str:
    """Убирает обёртку ``` или ```html в начале/конце ответа модели."""
    t = (text or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    while lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).strip()


def is_substantive_telegram_html_markup(s: str) -> bool:
    """
    Разметка похожа на готовую под Telegram HTML, без доп. LLM-прохода.

    Отличается от «широкой» эвристики: не срабатывает на одинокий тег <a> без href
    (sanitize тогда вырезает тег и текст выглядит «плоским»).
    """
    if not s or not str(s).strip():
        return False
    t = str(s).strip()
    if re.search(r"<\s*br\s*/?>", t, re.I):
        return True
    if re.search(r"</?(?:blockquote|pre|code|b|strong)\b", t, re.I):
        return True
    if re.search(r"</?(?:i|em|u|ins|s|strike|del)\b", t, re.I):
        return True
    if re.search(r'<\s*a\s+[^>]*\bhref\s*=\s*["\']', t, re.I):
        return True
    return False


def _segment_bold_markdown(seg: str) -> str:
    """Превращает **жирный** в <b>…</b>, остальное экранирует (кусок без code fence)."""
    import html as html_module

    out: list[str] = []
    pos = 0
    for m in re.finditer(r"\*\*([^*]+)\*\*", seg):
        out.append(html_module.escape(seg[pos : m.start()]))
        out.append("<b>" + html_module.escape(m.group(1)) + "</b>")
        pos = m.end()
    out.append(html_module.escape(seg[pos:]))
    return "".join(out)


def _markdownish_to_telegram_html(s: str) -> str:
    """Минимальная конверсия типичного вывода LLM: ```код``` и **жирный**."""
    import html as html_module

    out: list[str] = []
    last = 0
    for m in re.finditer(r"```(?:[^\n`]*\n)?(.*?)```", s, flags=re.DOTALL):
        pref = s[last : m.start()]
        out.append(_segment_bold_markdown(pref))
        code_body = (m.group(1) or "").strip("\n")
        out.append("<pre><code>" + html_module.escape(code_body) + "</code></pre>")
        last = m.end()
    out.append(_segment_bold_markdown(s[last:]))
    return "".join(out)


def should_use_llm_for_markdown_format(s: str) -> bool:
    """
    Стоит ли вызывать отдельный LLM-проход Markdown→HTML (вместо локальной эвристики).
    Уже похожий на Telegram HTML ответ — без доп. вызова.
    """
    if not s or not str(s).strip():
        return False
    t = s.strip()
    if _looks_like_telegram_markup(t):
        return False
    if "**" in t or "```" in t:
        return True
    if re.search(r"(?m)^#+\s", t):
        return True
    if re.search(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", t):
        return True
    if "__" in t and re.search(r"__(?!_)[^_]+__(?!_)", t):
        return True
    return False


def looks_like_telegram_html(s: str) -> bool:
    """То же, что :func:`is_substantive_telegram_html_markup` (узкое совпадение)."""
    return is_substantive_telegram_html_markup(s)


def normalize_llm_reply_for_telegram(text: str | None) -> str:
    """
    Ответ агента перед отправкой с parse_mode HTML.

    Если модель уже вернула теги — только ``sanitize_telegram_html``.
    Если похоже на markdown (** и ```) без HTML — конвертируем в подмножество Telegram HTML.
    Иначе экранируем как обычный текст (безопасная обёртка).
    """
    if not text:
        return ""
    s = normalize_fixed_markdown_phrases(text.strip())
    if is_substantive_telegram_html_markup(s):
        return sanitize_telegram_html(s)
    if "**" in s or "```" in s:
        return sanitize_telegram_html(_markdownish_to_telegram_html(s))
    if "<" in s and re.search(r"<\s*[a-z/!]", s, re.I):
        return sanitize_telegram_html(s)
    import html as html_module

    return sanitize_telegram_html(html_module.escape(s))


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

    return balance_telegram_html_tags(out)


# --- балансировка и разбиение длинных HTML-сообщений (parse_mode HTML) ---

_TAG_SPLIT_RE = re.compile(
    r"<(/?)\s*(b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote)\b([^>]*)>",
    re.IGNORECASE | re.DOTALL,
)

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


def balance_telegram_html_tags(html: str) -> str:
    if not html:
        return ""
    stack = _telegram_html_open_stack(html)
    if not stack:
        return html
    return html + "".join(_CLOSE_FOR_CANON[c] for c in reversed(stack) if c in _CLOSE_FOR_CANON)


def _balance_telegram_html_chunk_end(chunk: str) -> tuple[str, str]:
    open_stack = _telegram_html_open_stack(chunk)
    if not open_stack:
        return chunk, ""
    closer = "".join(_CLOSE_FOR_CANON[c] for c in reversed(open_stack) if c in _CLOSE_FOR_CANON)
    reopener = "".join(_OPEN_FOR_CANON[c] for c in open_stack if c in _OPEN_FOR_CANON)
    return chunk + closer, reopener


def split_telegram_html_message_chunks(html: str, max_len: int = 3500) -> list[str]:
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
    if not text:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def strip_subscribe_cta(text: str | None) -> Tuple[str, bool]:
    """Убирает маркер оплаты; возвращает (текст для пользователя, показывать ли кнопку)."""
    if not text:
        return "", False
    raw = text
    if CTA_SUBSCRIBE_MARKER not in raw:
        return raw.strip(), False
    cleaned = raw.replace(CTA_SUBSCRIBE_MARKER, "").strip()
    return cleaned, True
