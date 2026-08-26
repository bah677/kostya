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


def format_poll_intro_html(voice_count: int) -> str:
    n = int(voice_count)
    return (
        "<b>🎧 Помогите выбрать голос для молитвы</b>\n\n"
        "Мы хотим сделать голосовые молитвы ещё лучше — теплее, "
        "спокойнее и приятнее для прослушивания.\n\n"
        f"Ниже — {n} коротких образцов (~40 секунд) одной и той же молитвы, "
        "но разными голосами. Под каждым — кнопки оценки от "
        "<b>1</b> (совсем не подходит) до <b>5</b> (очень нравится).\n\n"
        "<b>Как слушать</b>\n"
        "• Обязательно на скорости <b>1×</b> — без ускорения. "
        "Иначе голос звучит не так, как будет в боте.\n"
        "• Можно слушать с наушниками или без — как удобнее.\n\n"
        "<b>Срок</b>\n"
        "• Голоса принимаем до <b>23:59 по Москве, 28 августа</b>.\n"
        "• До этого времени оценку можно поставить и в любой момент изменить.\n"
        "• После дедлайна подведём итоги — голос, который наберёт больше всего, "
        "станет основным для голосовых молитв.\n\n"
        "Спасибо, что помогаете — без вас мы бы не узнали, "
        "что по-настоящему откликается 💛"
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


def _voice_row_stats(
    voice: Dict[str, Any],
    aggregates: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    vid = str(voice["voice_id"])
    row = aggregates.get(vid) or {}
    cnt = int(row.get("vote_count") or 0)
    total = int(row.get("score_sum") or 0)
    avg = float(row.get("score_avg") or 0) if cnt else None
    return {
        "idx": int(voice["idx"]),
        "title": str(voice["title"]),
        "voice_id": vid,
        "vote_count": cnt,
        "score_sum": total,
        "score_avg": avg,
    }


def format_poll_scores_html(
    voices: Sequence[Dict[str, Any]],
    aggregates: Sequence[Dict[str, Any]],
    *,
    poll_slug: str = POLL_SLUG,
    participant_stats: Optional[Dict[str, int]] = None,
) -> str:
    by_vid = {str(r["voice_id"]): r for r in aggregates}
    voice_count = len(voices)
    stats = participant_stats or {}
    voters = int(stats.get("voters") or 0)
    complete = int(stats.get("complete_voters") or 0)
    partial = int(stats.get("partial_voters") or 0)
    total_ratings = int(stats.get("total_ratings") or 0)

    rows = [_voice_row_stats(v, by_vid) for v in voices]

    lines = [
        "<b>🎧 Опрос голосов молитвы</b>",
        f"Опрос <code>{html.escape(poll_slug)}</code> · вариантов: {voice_count}",
        "",
        "<b>📊 Участие</b>",
    ]
    if voters:
        lines.extend(
            [
                f"• Проголосовало: <b>{voters}</b> "
                f"{_people_word(voters)} (хотя бы одна оценка)",
                f"• Оценили все {voice_count}: <b>{complete}</b> "
                f"{_people_word(complete)}",
                f"• Частично (не все варианты): <b>{partial}</b> "
                f"{_people_word(partial)}",
                f"• Всего оценок: <b>{total_ratings}</b>",
            ]
        )
    else:
        lines.append("• Пока никто не проголосовал.")

    ranked = sorted(
        [r for r in rows if r["vote_count"] > 0],
        key=lambda r: (-(r["score_avg"] or 0), -r["vote_count"], r["idx"]),
    )
    lines.extend(["", "<b>🏆 Топ по среднему баллу</b>"])
    if ranked:
        medals = ("🥇", "🥈", "🥉")
        for pos, row in enumerate(ranked):
            medal = medals[pos] if pos < len(medals) else f"{pos + 1}."
            avg_s = f"{row['score_avg']:.2f}"
            lines.append(
                f"{medal} <b>{html.escape(row['title'])}</b> — "
                f"ср. <b>{avg_s}</b> "
                f"({row['vote_count']} {_votes_word(row['vote_count'])}, "
                f"сумма {row['score_sum']})"
            )
    else:
        lines.append("• Пока нет оценок.")

    lines.extend(["", "<b>📋 Все варианты</b>"])
    for row in rows:
        if row["vote_count"]:
            avg_s = f"{row['score_avg']:.2f}"
            detail = (
                f"ср. <b>{avg_s}</b>, {row['vote_count']} "
                f"{_votes_word(row['vote_count'])}, сумма {row['score_sum']}"
            )
        else:
            detail = "нет оценок"
        lines.append(
            f"{row['idx'] + 1}. <b>{html.escape(row['title'])}</b> — {detail}"
        )

    lines.append("")
    lines.append("<i>Шкала 1–5 под каждым образцом. Оценку можно менять до дедлайна.</i>")
    return "\n".join(lines)


def _people_word(n: int) -> str:
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return "человек"
    if n % 10 == 1:
        return "человек"
    if n % 10 in (2, 3, 4):
        return "человека"
    return "человек"


def _votes_word(n: int) -> str:
    n = abs(int(n))
    if n % 100 in (11, 12, 13, 14):
        return "оценок"
    if n % 10 == 1:
        return "оценка"
    if n % 10 in (2, 3, 4):
        return "оценки"
    return "оценок"
