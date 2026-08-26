"""Опрос голосов молитвы: список кандидатов, длинный образец, клавиатура оценок."""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

POLL_SLUG = "2026-08-27"
CB_PREFIX = "pvp"

# ~40 с озвучки при текущем atempo.
PRAYER_VOICE_POLL_SAMPLE = (
    "Отец Небесный, благодарю Тебя за этот день и за каждое дыхание. "
    "Ты знаешь моё сердце лучше, чем я сам знаю его. "
    "Укрепи меня там, где я слаб, и направь мысли туда, где нужен Твой мир. "
    "Если тревожно — напомни, что Ты ближе, чем страх. "
    "Если одиноко — напомни, что Ты слышишь даже безмолвную молитву. "
    "Пусть Твоё слово будет светом на моём пути, когда я не вижу дороги. "
    "Дай мудрость принимать решения не из гордости, а из любви. "
    "Прости то, что было сказано и сделано не по любви. "
    "Благослови близких, даже если между нами бывает трудно. "
    "Научи меня доверять Тебе не только словами, но и жизнью. "
    "Сохрани душу мою в истине, когда вокруг слишком много мнений. "
    "Пусть эта молитва будет не формальностью, а тихой встречей с Тобой. "
    "Аминь."
)

_EXTRA_VOICE_IDS: Sequence[str] = (
    "a4CnuaYbALRvW39mDitg",
    "ekJ0doQ5Wa25P7W5HCj7",
    "5KvpaGteYkNayiswuX2h",
    "oR4uRy4fHDUGGISL0Rev",
    "wAGzRVkxKEs8La0lmdrE",
    "8TMmdpPgqHKvDOGYP2lN",
    "C1npRmjB19a6yNkEucvx",
    "CritVAMVzFsSIWmMDe7v",
    "gMIlPNegT3C1SdNBp6rW",
    "ogi2DyUAKJb7CEdqqvlU",
    "TU2w9J6yEyVkPB7HKH2g",
    "q5RNAd4899271dg9W2K8",
)


def build_poll_voices(standard_voice_id: str) -> List[Dict[str, Any]]:
    """Список голосов: idx, voice_id, title (стандарт первым, без дублей)."""
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    std = (standard_voice_id or "").strip()
    if std:
        seen.add(std)
        out.append(
            {
                "idx": 0,
                "voice_id": std,
                "title": "Стандарт (текущий)",
            }
        )
    for raw in _EXTRA_VOICE_IDS:
        vid = raw.strip()
        if not vid or vid in seen:
            continue
        seen.add(vid)
        idx = len(out)
        out.append(
            {
                "idx": idx,
                "voice_id": vid,
                "title": f"Кандидат {idx} ({vid[:8]}…)",
            }
        )
    return out


def rating_callback(voice_idx: int, score: int) -> str:
    return f"{CB_PREFIX}:{voice_idx}:{score}"


def parse_rating_callback(data: str) -> Optional[Tuple[int, int]]:
    if not data.startswith(f"{CB_PREFIX}:"):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def build_rating_keyboard(
    voice_idx: int,
    *,
    selected: Optional[int] = None,
) -> InlineKeyboardMarkup:
    row: List[InlineKeyboardButton] = []
    for score in range(1, 6):
        label = f"✓ {score}" if selected == score else str(score)
        btn = InlineKeyboardButton(
            text=label,
            callback_data=rating_callback(voice_idx, score),
        )
        if selected == score:
            btn.style = "primary"
        row.append(btn)
    return InlineKeyboardMarkup(inline_keyboard=[row])


def format_poll_scores_html(
    voices: Sequence[Dict[str, Any]],
    aggregates: Sequence[Dict[str, Any]],
    *,
    poll_slug: str = POLL_SLUG,
) -> str:
    by_vid = {str(r["voice_id"]): r for r in aggregates}
    lines = [
        "<b>🎧 Оценки голосов молитвы</b>",
        f"Опрос: <code>{html.escape(poll_slug)}</code>",
        "",
        "<b>Сводка</b> (сумма баллов / число оценок, среднее):",
    ]
    for v in voices:
        vid = str(v["voice_id"])
        row = by_vid.get(vid) or {}
        total = int(row.get("score_sum") or 0)
        cnt = int(row.get("vote_count") or 0)
        avg = float(row.get("score_avg") or 0)
        avg_s = f"{avg:.2f}" if cnt else "—"
        lines.append(
            f"{v['idx'] + 1}. <b>{html.escape(str(v['title']))}</b>\n"
            f"   <code>{html.escape(vid)}</code>\n"
            f"   баллы: <b>{total}</b> / голосов: {cnt}, ср. {avg_s}"
        )
    lines.append("")
    lines.append("<i>Оценка 1–5 под каждым образцом. Можно менять.</i>")
    return "\n".join(lines)
