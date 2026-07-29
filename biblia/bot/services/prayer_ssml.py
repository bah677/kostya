"""SSML и pause-plain для озвучки молитв (единая логика пауз/просодии)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List

from bot.services.voicebox_tts import format_prayer_for_tts

# Слова для мягкого смыслового выделения в SSML.
_EMPHASIS_WORDS = frozenset(
    {
        "господи",
        "господь",
        "боже",
        "отец",
        "отца",
        "сын",
        "сына",
        "дух",
        "духа",
        "христе",
        "христос",
        "аминь",
        "аминь.",
    }
)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_WORD_RE = re.compile(r"\S+|\s+")

# Паузы по умолчанию (как в шаблоне молитвы).
_BREAK_BETWEEN_SENTENCES = "700ms"
_BREAK_BETWEEN_PARAS = "1s"
_BREAK_BEFORE_AMEN = "500ms"


@dataclass(frozen=True)
class PrayerSpeechMarkup:
    """Один текст молитвы → формы для разных TTS."""

    plain: str
    """Для Voicebox / OpenAI: plain с паузами через абзацы и многоточия."""

    ssml: str
    """Для Yandex SpeechKit: полный SSML в <speak>."""

    instruct_extra: str
    """Доп. инструкции пауз/темпа для OpenAI (и Voicebox instruct)."""


def _split_paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    flat = re.sub(r"\s+", " ", (text or "").strip())
    return [flat] if flat else []


def _split_sentences(paragraph: str) -> List[str]:
    bits = [b.strip() for b in _SENT_SPLIT_RE.split(paragraph) if b.strip()]
    return bits if bits else ([paragraph.strip()] if paragraph.strip() else [])


def _is_amen_sentence(s: str) -> bool:
    t = re.sub(r"[^\wа-яёА-ЯЁ]+", "", s, flags=re.I).lower()
    return t in {"аминь", "аминьь"} or t.endswith("аминь")


def _ssml_escape(s: str) -> str:
    return html.escape(s, quote=True)


def _ssml_phrase(sentence: str) -> str:
    """Оборачивает ключевые слова в <emphasis>."""
    parts: List[str] = []
    for tok in _WORD_RE.findall(sentence):
        if tok.isspace():
            parts.append(tok)
            continue
        core = re.sub(r"^[«\"'(]+|[»\"').,:;!?…]+$", "", tok)
        key = core.lower()
        if key in _EMPHASIS_WORDS or key.rstrip(".") in _EMPHASIS_WORDS:
            parts.append(
                f'<emphasis level="moderate">{_ssml_escape(tok)}</emphasis>'
            )
        else:
            parts.append(_ssml_escape(tok))
    return "".join(parts)


def _collect_sentences(text: str) -> List[List[str]]:
    paras = _split_paragraphs(text)
    return [_split_sentences(p) for p in paras if _split_sentences(p)]


def build_prayer_speech_markup(
    text: str,
    *,
    rate: str | None = None,
    pitch: str | None = None,
    sentence_break: str = _BREAK_BETWEEN_SENTENCES,
    paragraph_break: str = _BREAK_BETWEEN_PARAS,
    amen_break: str = _BREAK_BEFORE_AMEN,
) -> PrayerSpeechMarkup:
    """
    Превращает текст молитвы в SSML + pause-plain + доп. instruct.

    Текст сначала нормализуется через ``format_prayer_for_tts``.
    """
    from config import config

    rate = (rate or getattr(config, "PRAYER_TTS_SSML_RATE", None) or "85%").strip()
    pitch = (pitch or getattr(config, "PRAYER_TTS_SSML_PITCH", None) or "-3%").strip()

    body = format_prayer_for_tts(text)
    groups = _collect_sentences(body)
    if not groups:
        empty = (body or "").strip()
        return PrayerSpeechMarkup(
            plain=empty,
            ssml=f"<speak>{_ssml_escape(empty)}</speak>" if empty else "<speak></speak>",
            instruct_extra="",
        )

    # --- SSML ---
    ssml_paras: List[str] = []
    for gi, sents in enumerate(groups):
        chunks: List[str] = []
        for si, sent in enumerate(sents):
            if si > 0:
                br = amen_break if _is_amen_sentence(sent) else sentence_break
                chunks.append(f'<break time="{br}"/>')
            chunks.append(f"<s>{_ssml_phrase(sent)}</s>")
        block = "<p>\n      " + "\n      ".join(chunks) + "\n    </p>"
        ssml_paras.append(block)
        if gi < len(groups) - 1:
            ssml_paras.append(f'<break time="{paragraph_break}"/>')

    ssml = (
        "<speak>\n"
        f'  <prosody rate="{_ssml_escape(rate)}" pitch="{_ssml_escape(pitch)}">\n'
        "    "
        + "\n    ".join(ssml_paras)
        + "\n"
        "  </prosody>\n"
        "</speak>"
    )

    # --- plain с паузами (для движков без SSML) ---
    plain_paras: List[str] = []
    for sents in groups:
        line_parts: List[str] = []
        for si, sent in enumerate(sents):
            if si > 0:
                line_parts.append("…")
            line_parts.append(sent)
        plain_paras.append(" ".join(line_parts))
    plain_blocks: List[str] = []
    for i, para in enumerate(plain_paras):
        plain_blocks.append(para)
        if i < len(plain_paras) - 1:
            plain_blocks.append("…")
    plain = "\n\n".join(plain_blocks)

    instruct_extra = (
        "Delivery as a real prayer, not an announcement. "
        f"Overall pace about {rate}, pitch slightly lower ({pitch}). "
        "Pause briefly between sentences (~0.5–0.7s) and longer between "
        "paragraphs (~1s). Softly emphasize words like Господи, Боже, Аминь. "
        "Do not rush; leave silence where the text has ellipsis (…)."
    )

    return PrayerSpeechMarkup(plain=plain, ssml=ssml, instruct_extra=instruct_extra)


def prayer_ssml_enabled() -> bool:
    from config import config

    return bool(getattr(config, "PRAYER_TTS_SSML_ENABLED", True))


def prepare_prayer_for_engine(text: str, *, engine: str) -> tuple[str, str]:
    """
    Готовит payload для TTS.

    Returns:
        (body, mode) где mode in {"ssml", "plain"}.
    """
    if not prayer_ssml_enabled():
        body = format_prayer_for_tts(text)
        return body, "plain"

    markup = build_prayer_speech_markup(text)
    eng = (engine or "").strip().lower()
    if eng in {"yandex", "speechkit", "yandex_speechkit", "salute", "salutespeech", "sber"}:
        return markup.ssml, "ssml"
    return markup.plain, "plain"


def adapt_ssml_for_yandex(raw_ssml: str) -> str:
    """
    SpeechKit v1 не принимает <prosody> в текущем TTS endpoint.
    Оставляем совместимые теги и выносим темп в post-processing.
    """
    raw = (raw_ssml or "").strip()
    if not raw:
        return "<speak></speak>"
    adapted = re.sub(r"<prosody\b[^>]*>\s*", "", raw, flags=re.I)
    adapted = re.sub(r"\s*</prosody>", "", adapted, flags=re.I)
    return adapted


def resolve_prayer_tts_instruct_with_ssml() -> str:
    """Базовый instruct + доп. guidance из SSML-логики (для OpenAI/Voicebox)."""
    from bot.services.prayer_tts_style import resolve_prayer_tts_instruct

    base = resolve_prayer_tts_instruct()
    if not prayer_ssml_enabled():
        return base
    extra = build_prayer_speech_markup("Аминь.").instruct_extra
    if not extra:
        return base
    if extra in base:
        return base
    return f"{base} {extra}".strip()
