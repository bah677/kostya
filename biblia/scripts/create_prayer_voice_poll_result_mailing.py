#!/usr/bin/env python3
"""Черновик рассылки участникам опроса голосов молитвы + превью SUPER_ADMIN."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from subprocess import run

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.services.prayer_voice_poll import POLL_SLUG, build_poll_voices  # noqa: E402
from storage.user_storage import UserStorage  # noqa: E402

WINNER_VOICE_ID = "a4CnuaYbALRvW39mDitg"
# Голос «Стандарт» на момент опроса (до установки победителя).
POLL_STANDARD_VOICE_ID = "q5RNAd4899271dg9W2K8"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default="/home/appuser/biblia/.env")
    p.add_argument(
        "--name",
        default="Итоги опроса голоса молитвы 2026-08-28",
    )
    return p.parse_args()


def _build_mailing_text(*, winner_title: str, winner_avg: float, runner_avg: float) -> str:
    gap = winner_avg - runner_avg
    gap_line = (
        f"По средней оценке он опередил ближайший вариант "
        f"примерно на <b>{gap:.1f}</b> балла — заметный отрыв."
        if gap >= 0.3
        else "По средней оценке он набрал больше всего."
    )
    return (
        "<b>Спасибо, что помогли выбрать голос для молитвы</b> 🙏\n\n"
        "Мы подвели итоги опроса. Вы слушали образцы, ставили оценки — "
        "без этого мы бы гадали вслепую.\n\n"
        f"Победил голос из опроса — «<b>{winner_title}</b>». {gap_line}\n\n"
        "Мы уже установили его как основной для голосовых молитв в боте. "
        "Можете попробовать — кнопка ниже или команда /prayer.\n\n"
        "Спасибо, что вы с нами 💛"
    )


async def _load_poll_stats(storage: UserStorage) -> tuple[list[int], str, float, float]:
    voices = build_poll_voices(POLL_STANDARD_VOICE_ID)
    voice_count = len(voices)
    agg = await storage.list_prayer_voice_ratings_aggregate(POLL_SLUG)
    if not agg:
        raise SystemExit(f"Нет оценок для poll_slug={POLL_SLUG}")

    by_id = {str(r["voice_id"]): r for r in agg}
    winner_row = by_id.get(WINNER_VOICE_ID)
    if not winner_row:
        raise SystemExit(f"Нет оценок для победителя {WINNER_VOICE_ID}")

    winner_title = next(
        (v["title"] for v in voices if v["voice_id"] == WINNER_VOICE_ID),
        "Кандидат 1",
    )
    winner_avg = float(winner_row["score_avg"] or 0)

    others = [
        float(r["score_avg"] or 0)
        for vid, r in by_id.items()
        if vid != WINNER_VOICE_ID
    ]
    runner_avg = max(others) if others else 0.0

    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT admin_user_id
              FROM prayer_voice_ratings
             WHERE poll_slug = $1
             ORDER BY 1
            """,
            POLL_SLUG,
        )
    uids = [int(r["admin_user_id"]) for r in rows if int(r["admin_user_id"]) > 0]
    if not uids:
        raise SystemExit("Пустой список участников опроса")

    stats = await storage.get_prayer_voice_poll_participant_stats(
        POLL_SLUG, voice_count=voice_count
    )
    print(
        f"poll={POLL_SLUG} voters={stats['voters']} "
        f"complete={stats['complete_voters']} winner={winner_title} "
        f"avg={winner_avg:.2f} runner={runner_avg:.2f} audience={len(uids)}"
    )
    return uids, winner_title, winner_avg, runner_avg


async def main() -> None:
    args = _parse_args()
    load_dotenv(args.env, override=True)

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
        f"/{os.getenv('DB_NAME') or os.getenv('BIBLIA_DB_NAME')}"
    )
    storage = UserStorage(db_url)
    await storage.initialize()
    try:
        uids, winner_title, winner_avg, runner_avg = await _load_poll_stats(storage)
        text = _build_mailing_text(
            winner_title=winner_title,
            winner_avg=winner_avg,
            runner_avg=runner_avg,
        )
    finally:
        await storage.close()

    with tempfile.TemporaryDirectory() as tmp:
        users_file = Path(tmp) / "poll_voters.txt"
        text_file = Path(tmp) / "poll_result_mail.txt"
        users_file.write_text("\n".join(str(u) for u in uids) + "\n", encoding="utf-8")
        text_file.write_text(text + "\n", encoding="utf-8")

        cmd = [
            str(ROOT / "venv" / "bin" / "python"),
            str(ROOT / "scripts" / "create_mailing_draft_preview.py"),
            "--env",
            args.env,
            "--name",
            args.name,
            "--users-file",
            str(users_file),
            "--text-file",
            str(text_file),
            "--button-text",
            "🙏 Составить молитву",
            "--button-callback",
            "prayer_start",
        ]
        proc = run(cmd, cwd=str(ROOT), check=False)
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)


if __name__ == "__main__":
    asyncio.run(main())
