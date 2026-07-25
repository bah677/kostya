"""Зеркало ответов topic-assist в отдельную форум-группу (пилот-контроль)."""

from __future__ import annotations

import html as html_mod
import json
import logging
from pathlib import Path
from typing import Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode

from config import config

logger = logging.getLogger(__name__)

# кейс ответа → имя топика
MIRROR_CASES = {
    "ephemeral": "CTA · ephemeral",
    "public": "CTA · public",
    "error": "CTA · errors",
}

_CACHE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "club_topic_assist_mirror_topics.json"
)


class TopicAssistMirror:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._topics: Dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        try:
            if _CACHE_PATH.is_file():
                raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._topics = {
                        str(k): int(v) for k, v in raw.items() if v
                    }
        except Exception as e:
            logger.warning("mirror topics load: %s", e)

    def _save(self) -> None:
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(
                json.dumps(self._topics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("mirror topics save: %s", e)

    @property
    def enabled(self) -> bool:
        return bool(
            config.CLUB_TOPIC_ASSIST_MIRROR_ENABLED
            and int(config.CLUB_TOPIC_ASSIST_MIRROR_GROUP_ID or 0) > 0
        )

    async def ensure_topics(self) -> None:
        if not self.enabled:
            return
        gid = int(config.CLUB_TOPIC_ASSIST_MIRROR_GROUP_ID)
        for case, title in MIRROR_CASES.items():
            if self._topics.get(case):
                continue
            try:
                topic = await self.bot.create_forum_topic(chat_id=gid, name=title[:128])
                tid = int(topic.message_thread_id)
                self._topics[case] = tid
                logger.info("mirror topic created case=%s thread=%s (%s)", case, tid, title)
            except Exception as e:
                logger.error("create mirror topic %s: %s", case, e)
        self._save()

    async def post(
        self,
        *,
        case: str,
        user_id: int,
        user_name: str,
        context_tail: str,
        question: str,
        answer: str,
        classify_reason: str = "",
        extra: str = "",
    ) -> Optional[int]:
        if not self.enabled:
            return None
        await self.ensure_topics()
        tid = self._topics.get(case) or self._topics.get("error")
        if not tid:
            logger.warning("no mirror topic for case=%s", case)
            return None
        gid = int(config.CLUB_TOPIC_ASSIST_MIRROR_GROUP_ID)
        name = html_mod.escape(user_name or str(user_id))
        parts = [
            f"<b>case:</b> <code>{html_mod.escape(case)}</code>",
            f"<b>user:</b> {name} (<code>{user_id}</code>)",
        ]
        if classify_reason:
            parts.append(
                f"<b>classify:</b> {html_mod.escape(classify_reason[:400])}"
            )
        if context_tail:
            parts.extend(
                [
                    "",
                    "<b>context (last msgs):</b>",
                    f"<pre>{html_mod.escape(context_tail[:2500])}</pre>",
                ]
            )
        parts.extend(
            [
                "",
                "<b>question:</b>",
                html_mod.escape(question[:1500]),
                "",
                "<b>answer:</b>",
                html_mod.escape(answer[:3500]),
            ]
        )
        if extra:
            parts.extend(["", f"<i>{html_mod.escape(extra[:500])}</i>"])
        text = "\n".join(parts)
        try:
            msg = await self.bot.send_message(
                chat_id=gid,
                text=text,
                message_thread_id=tid,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return int(msg.message_id)
        except Exception as e:
            logger.warning("mirror post failed case=%s: %s", case, e)
            return None
