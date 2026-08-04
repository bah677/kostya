"""Доставка роликов в топик закрытой группы аватара."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Sequence

from aiogram.types import FSInputFile

from youtube_prayer.covers import CoverPack
from youtube_prayer.render import ShortClip
from youtube_prayer.trends import PrayerTopic

logger = logging.getLogger(__name__)


async def deliver_topic_pack(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    topic: PrayerTopic,
    day: str,
    index: int,
    prayer_text: str,
    horizontal: Path,
    shorts: Sequence[ShortClip],
    ogg: Optional[Path] = None,
    lang: str = "ru",
    covers: Optional[CoverPack] = None,
) -> None:
    """Шлёт текст + обложки + горизонталь + 3 шортса (+ опц. voice) в forum topic."""
    lang_l = (lang or "ru").lower()
    lang_tag = "EN · US" if lang_l == "en" else "RU"
    cover_title = covers.title if covers else ""
    header = (
        f"🙏 <b>YouTube-молитва</b> · {lang_tag} · {day} · #{index}\n"
        f"<b>Тренд:</b> { _esc(topic.trend) }\n"
        f"<b>Бриф:</b> { _esc(topic.brief) }\n"
        + (f"<b>Обложка:</b> {_esc(cover_title)}\n" if cover_title else "")
        + f"<b>B-roll:</b> <code>{_esc(topic.broll_query)}</code>"
    )
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}

    await bot.send_message(chat_id, header, parse_mode="HTML", **kwargs)

    if covers is not None:
        try:
            if covers.horizontal.is_file():
                await bot.send_photo(
                    chat_id,
                    FSInputFile(str(covers.horizontal), filename="cover_16x9.jpg"),
                    caption=f"🖼 Обложка 16:9 · {_esc(covers.title)[:200]}",
                    **kwargs,
                )
            if covers.vertical.is_file():
                await bot.send_photo(
                    chat_id,
                    FSInputFile(str(covers.vertical), filename="cover_9x16.jpg"),
                    caption=f"🖼 Обложка 9:16 · {_esc(covers.title)[:200]}",
                    **kwargs,
                )
        except Exception as e:
            logger.warning("send covers failed: %s", e)

    # текст молитвы (обрезать если очень длинный)
    body = (prayer_text or "").strip()
    if len(body) > 3500:
        body = body[:3400] + "…"
    await bot.send_message(
        chat_id,
        f"<b>Текст молитвы</b>\n\n{_esc(body)}",
        parse_mode="HTML",
        **kwargs,
    )

    if ogg and ogg.is_file() and ogg.stat().st_size < 20_000_000:
        try:
            await bot.send_voice(
                chat_id,
                FSInputFile(str(ogg), filename=f"prayer_{index}.ogg"),
                caption=f"Аудио · #{index} · {_esc(topic.trend)[:60]}",
                **kwargs,
            )
        except Exception as e:
            logger.warning("send_voice failed: %s", e)

    await _send_video_or_doc(
        bot,
        chat_id,
        horizontal,
        caption=f"16:9 полная · #{index} · {_esc(topic.trend)[:80]}",
        kwargs=kwargs,
    )
    for sc in shorts:
        await _send_video_or_doc(
            bot,
            chat_id,
            sc.path,
            caption=(
                f"9:16 шорт {sc.index}/3 · #{index} · "
                f"{sc.start_sec:.0f}–{sc.end_sec:.0f}с"
            ),
            kwargs=kwargs,
        )


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def _send_video_or_doc(
    bot: Any,
    chat_id: int,
    path: Path,
    *,
    caption: str,
    kwargs: dict,
) -> None:
    if not path.is_file():
        logger.error("missing file %s", path)
        return
    size = path.stat().st_size
    file = FSInputFile(str(path), filename=path.name)
    try:
        if size < 48_000_000:
            await bot.send_video(chat_id, file, caption=caption[:1024], **kwargs)
        else:
            await bot.send_document(chat_id, file, caption=caption[:1024], **kwargs)
    except Exception as e:
        logger.warning("send_video failed (%s), try document: %s", path.name, e)
        try:
            await bot.send_document(
                chat_id,
                FSInputFile(str(path), filename=path.name),
                caption=caption[:1024],
                **kwargs,
            )
        except Exception as e2:
            logger.error("send_document failed %s: %s", path.name, e2)


async def deliver_run_summary(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    day: str,
    lines: List[str],
) -> None:
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
    text = f"✅ <b>YouTube-молитвы за {day}</b>\n" + "\n".join(lines)
    await bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
