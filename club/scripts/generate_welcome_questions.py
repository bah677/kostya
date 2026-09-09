#!/usr/bin/env python3
"""
Сгенерировать пул вопросов для приветствия новичка (ЧАСТЬ А ВОЛ-2)
через LLM с контекстом клуба и отправить админам на контроль.

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/generate_welcome_questions.py --dry-run
  ./venv/bin/python scripts/generate_welcome_questions.py --send-admins
  ./venv/bin/python scripts/generate_welcome_questions.py --send-admins --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from aiogram import Bot
from aiogram.enums import ParseMode

from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("generate_welcome_questions")

DEFAULT_PROD_ENV = "/home/appuser/club/.env"
ABOUT_PATH = Path(__file__).resolve().parents[1] / "bot" / "texts" / "aboutclub.txt"
TEXTS_PATH = Path(__file__).resolve().parents[1] / "bot" / "texts" / "ru_gift_wave.py"

SYSTEM = """\
Ты копирайтер закрытого клуба «Любящие Бога» (Константин).
Нужен список из 14 коротких вопросов для приветствия новичка в группе.

Правила ТЗ:
- вопрос допускает ответ одной строкой;
- про человека, не про клуб, тарифы, эфиры, бота;
- тёплый тон семьи, без назидания и «инфоцыганства»;
- регистр как у новичков: имя/город, чего ищут в сердце, отношения с Богом;
- без эмодзи в самих вопросах (или максимум один в конце всего списка — лучше без);
- на «ты»;
- разнообразные, без почти-дублей.

Верни СТРОГО JSON: {"questions": ["...", ...]} ровно 14 строк.
"""


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    return load_config()


def _about_snippet(limit: int = 3500) -> str:
    raw = ABOUT_PATH.read_text(encoding="utf-8", errors="ignore")
    # убрать «ĸ» артефакты для промпта
    raw = raw.replace("ĸ", "к")
    return raw[:limit]


async def generate_questions(api_key: str) -> List[str]:
    user = (
        "Контекст клуба (сокращённо):\n"
        f"{_about_snippet(1800)}\n\n"
        "Образцы того, что новички пишут сами: имя, город, "
        "«хочу побороть страх», «хочу, чтобы Бог был в моей жизни».\n"
        "Сгенерируй 14 вопросов."
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        if not content:
            raise RuntimeError(f"empty LLM content: {json.dumps(data)[:800]}")
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    # вытащить JSON-объект, если вокруг текст
    m = re.search(r"\{[\s\S]*\}", content)
    if m:
        content = m.group(0)
    parsed = json.loads(content)
    qs = [str(q).strip() for q in parsed.get("questions") or [] if str(q).strip()]
    if len(qs) < 12:
        raise RuntimeError(f"too few questions: {len(qs)} raw={content[:400]}")
    return qs[:15]


def write_questions_to_texts(questions: List[str]) -> None:
    text = TEXTS_PATH.read_text(encoding="utf-8")
    lines = ",\n".join(f'    "{q.replace(chr(34), chr(39))}"' for q in questions)
    block = (
        "# Вопросы в приветствии группы: по кругу (user_id % len).\n"
        "# Одна строка ответа, про человека — не про клуб.\n"
        "# Сгенерировано LLM + контроль админов.\n"
        f"WELCOME_QUESTION_POOL = (\n{lines},\n)"
    )
    new_text, n = re.subn(
        r"# Вопросы в приветствии группы:.*?\nWELCOME_QUESTION_POOL = \(.*?\n\)",
        block,
        text,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise RuntimeError("не нашёл WELCOME_QUESTION_POOL для замены")
    TEXTS_PATH.write_text(new_text, encoding="utf-8")


def format_admin_html(questions: List[str]) -> str:
    body = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return (
        "<b>Контроль: вопросы в приветствии новичка</b>\n\n"
        "Это пул для ЧАСТЬ А (ВОЛ-2): бот после упоминания задаёт "
        "<b>один</b> вопрос из списка (по кругу от user_id).\n"
        "Без LLM в рантайме. Нужен ваш взгляд: оставить / выкинуть / переписать.\n\n"
        f"<pre>{body}</pre>\n\n"
        "Если ок — ничего делать не надо (уже можно катить). "
        "Правки — напишите Константину текстом."
    )


async def send_admins(bot: Bot, storage: UserStorage, html: str, super_admin_id: int) -> int:
    ids = set()
    for row in await storage.list_telegram_admin_ids():
        ids.add(int(row["telegram_user_id"]))
    if super_admin_id:
        ids.add(int(super_admin_id))
    ok = 0
    for uid in sorted(ids):
        try:
            await bot.send_message(
                uid, html, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            ok += 1
        except Exception as e:
            logger.error("admin %s: %s", uid, e)
        await asyncio.sleep(0.3)
    return ok


async def run(args: argparse.Namespace) -> int:
    cfg = _load_config(args.env_file)
    key = (cfg.DEEPSEEK_API_KEY or "").strip()
    if not key:
        logger.error("нет DEEPSEEK_API_KEY")
        return 1
    questions = await generate_questions(key)
    html = format_admin_html(questions)
    if args.dry_run:
        print(html.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", ""))
        return 0
    if args.write:
        write_questions_to_texts(questions)
        print(f"Записано в {TEXTS_PATH}: {len(questions)} вопросов")
    if args.send_admins:
        bot = Bot(token=cfg.MIRON_BOT_TOKEN)
        storage = UserStorage(cfg.database_url)
        await storage.connect()
        try:
            n = await send_admins(bot, storage, html, int(cfg.SUPER_ADMIN_ID or 0))
            print(f"Отправлено админам: {n}")
        finally:
            await bot.session.close()
            await storage.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", default=DEFAULT_PROD_ENV)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send-admins", action="store_true")
    p.add_argument("--write", action="store_true", help="Записать в ru_gift_wave.py")
    args = p.parse_args()
    if not args.dry_run and not args.send_admins and not args.write:
        p.error("нужен --dry-run и/или --send-admins и/или --write")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
