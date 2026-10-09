"""Команды /yt_prayer, /yt_shorts, /yt_run и ночные прогоны по расписанию.

Горизонтальные ролики 16:9 и вертикальные Shorts 9:16 разведены полностью:
у каждого свой счётчик, свой час старта и свои слоты премьер. Счётчик 0
означает «не делать», поэтому любая комбинация собирается двумя цифрами
в .env: только шортсы, только горизонтальные, 3 + 5 и так далее.

Если часы старта совпадают, оба вида идут одним прогоном подряд (как было
раньше) — иначе они конкурировали бы за ffmpeg. Разные часы дают два
независимых расписания.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.filters.private_only import PRIVATE_CHAT
from config import config
from youtube_prayer.pipeline import run_daily_youtube_prayer_pipeline

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

KIND_HORIZ = "horiz"
KIND_SHORTS = "shorts"
_KIND_TITLE = {
    KIND_HORIZ: "горизонтальные 16:9",
    KIND_SHORTS: "Shorts 9:16",
}


def _horiz_hour() -> int:
    return int(getattr(config, "YT_PRAYER_HOUR_MSK", 3) or 3) % 24


def _shorts_hour() -> int:
    """Не задан — шортсы идут тем же прогоном, следом за горизонтальными."""
    raw = getattr(config, "YT_SHORTS_HOUR_MSK", None)
    if raw is None:
        return _horiz_hour()
    return int(raw) % 24


def _parse_args(raw: str) -> Tuple[bool, Optional[int]]:
    """«force», «3», «force 3» — в любом порядке.

    «1» раньше означало force; теперь это количество, иначе не отличить
    «сделай один ролик» от «запусти принудительно».
    """
    force = False
    count: Optional[int] = None
    for token in (raw or "").lower().split():
        if token in {"force", "forced", "f", "yes", "y"}:
            force = True
        elif token.isdigit():
            count = int(token)
    return force, count


def _abs_dir(raw: Optional[str], default: str) -> Path:
    p = Path(raw or default)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[2] / p
    return p


class YoutubePrayerFeature(BaseFeature):
    name = "youtube_prayer"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._tasks: List[asyncio.Task] = []
        self._run_lock = asyncio.Lock()

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    # ── регистрация ──────────────────────────────────────────────────────

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        if not getattr(config, "YT_PRAYER_ENABLED", True):
            self.log("YT_PRAYER_ENABLED=0 — хендлеры не регистрируются")
            return
        dispatcher.message.register(
            self.cmd_horiz, PRIVATE_CHAT, Command("yt_prayer")
        )
        dispatcher.message.register(
            self.cmd_horiz, PRIVATE_CHAT, Command("yt_prayer_run")
        )
        dispatcher.message.register(
            self.cmd_shorts, PRIVATE_CHAT, Command("yt_shorts")
        )
        dispatcher.message.register(self.cmd_all, PRIVATE_CHAT, Command("yt_run"))
        self.log("/yt_prayer, /yt_shorts, /yt_run зарегистрированы")

    async def start_background_tasks(self) -> None:
        # Очередь голосовых в канал Shorts — даже если ночной cron выключен:
        # иначе /yt_shorts force поставит job'ы, а отправлять будет некому.
        if (
            self._app
            and getattr(config, "YT_SHORTS_TG_CHANNEL_ENABLED", True)
            and int(getattr(config, "YT_SHORTS_TG_CHANNEL_ID", 0) or 0)
        ):
            already = any(
                t.get_name() == "yt_shorts_tg_voice_poll" and not t.done()
                for t in self._tasks
            )
            if not already:
                from youtube_shorts.tg_channel_publish import tg_voice_poll_loop

                work = _abs_dir(
                    getattr(config, "YT_SHORTS_WORK_DIR", None),
                    "data/youtube_shorts",
                )
                self._tasks.append(
                    asyncio.create_task(
                        tg_voice_poll_loop(self._app.bot, work_root=work),
                        name="yt_shorts_tg_voice_poll",
                    )
                )
                self.log(
                    "TG voice poll → chat "
                    f"{getattr(config, 'YT_SHORTS_TG_CHANNEL_ID', 0)}"
                )

        if not getattr(config, "YT_PRAYER_ENABLED", True):
            return
        if not getattr(config, "YT_SCHEDULE_ENABLED", True):
            # Команды остаются доступны — просто никто не будит их по часам.
            self.log("YT_SCHEDULE_ENABLED=0 — ночные прогоны не запускаются")
            return
        # Ночные loops — не дублируем, если уже подняты (poller выше мог добавить task).
        if any(
            (t.get_name() or "").startswith("youtube_daily_") and not t.done()
            for t in self._tasks
        ):
            return
        h_hour, s_hour = _horiz_hour(), _shorts_hour()
        if h_hour == s_hour:
            self._tasks.append(
                asyncio.create_task(
                    self._daily_loop(h_hour, (KIND_HORIZ, KIND_SHORTS)),
                    name="youtube_daily_all",
                )
            )
            self.log(f"ночной прогон {h_hour:02d}:00 MSK: горизонтальные + Shorts")
        else:
            self._tasks.append(
                asyncio.create_task(
                    self._daily_loop(h_hour, (KIND_HORIZ,)),
                    name="youtube_daily_horiz",
                )
            )
            self._tasks.append(
                asyncio.create_task(
                    self._daily_loop(s_hour, (KIND_SHORTS,)),
                    name="youtube_daily_shorts",
                )
            )
            self.log(
                f"ночные прогоны: горизонтальные {h_hour:02d}:00, "
                f"Shorts {s_hour:02d}:00 MSK"
            )

    async def stop_background_tasks(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks = []

    # ── команды ──────────────────────────────────────────────────────────

    async def cmd_horiz(self, message: Message, command: CommandObject) -> None:
        await self._cmd(message, command, kinds=(KIND_HORIZ,))

    async def cmd_shorts(self, message: Message, command: CommandObject) -> None:
        await self._cmd(message, command, kinds=(KIND_SHORTS,))

    async def cmd_all(self, message: Message, command: CommandObject) -> None:
        await self._cmd(message, command, kinds=(KIND_HORIZ, KIND_SHORTS))

    async def _cmd(
        self,
        message: Message,
        command: CommandObject,
        *,
        kinds: Sequence[str],
    ) -> None:
        if message.from_user is None:
            return
        if not await self._is_admin(message.from_user.id):
            return
        force, count = _parse_args(command.args or "")
        counts = {k: count for k in kinds} if count is not None else {}
        what = " + ".join(_KIND_TITLE[k] for k in kinds)
        plan = ", ".join(
            f"{_KIND_TITLE[k]}: {self._count_for(k, counts)}" for k in kinds
        )
        await message.answer(
            f"Запускаю: {what}"
            + (" (force)" if force else "")
            + f"\n{plan}\nЭто может занять от 15 минут до нескольких часов."
        )
        asyncio.create_task(
            self._run(
                kinds=kinds,
                force=force,
                progress_chat_id=message.chat.id,
                counts=counts,
                wait=False,
            ),
            name=f"yt_manual_{'_'.join(kinds)}",
        )

    # ── расписание ───────────────────────────────────────────────────────

    async def _daily_loop(self, hour: int, kinds: Sequence[str]) -> None:
        label = ",".join(kinds)
        while True:
            try:
                delay = _seconds_until_msk(hour, 0)
                logger.info(
                    "[%s] sleep %.0fs until %02d:00 MSK (%s)",
                    self.name,
                    delay,
                    hour,
                    label,
                )
                await asyncio.sleep(delay)
                # wait=True: если второй прогон ещё идёт, дожидаемся его,
                # а не пропускаем сегодняшний запуск молча.
                await self._run(
                    kinds=kinds, force=False, progress_chat_id=None, wait=True
                )
                # защита от повторного срабатывания в ту же минуту
                await asyncio.sleep(70)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("[%s] daily loop error: %s", self.name, e)
                await asyncio.sleep(300)

    # ── запуск ───────────────────────────────────────────────────────────

    @staticmethod
    def _count_for(kind: str, counts: Dict[str, int]) -> int:
        if kind in counts:
            return max(0, int(counts[kind]))
        if kind == KIND_HORIZ:
            return int(getattr(config, "YT_PRAYER_COUNT", 3) or 0)
        if not getattr(config, "YT_SHORTS_ENABLED", True):
            return 0
        return int(getattr(config, "YT_SHORTS_COUNT", 8) or 0)

    async def _run(
        self,
        *,
        kinds: Sequence[str],
        force: bool,
        progress_chat_id: Optional[int],
        counts: Optional[Dict[str, int]] = None,
        wait: bool = False,
    ) -> None:
        counts = counts or {}
        if not wait and self._run_lock.locked():
            await self._say(
                progress_chat_id, "Пайплайн уже выполняется — дождитесь окончания."
            )
            return
        async with self._run_lock:
            if not self._app:
                return
            chat_id = int(getattr(config, "YT_PRAYER_CHAT_ID", 0) or 0)
            topic_id = int(getattr(config, "YT_PRAYER_TOPIC_ID", 0) or 0)
            if not chat_id:
                logger.error("[%s] YT_PRAYER_CHAT_ID не задан", self.name)
                return
            history_days = int(
                getattr(config, "YT_PRAYER_TREND_HISTORY_DAYS", 14) or 14
            )
            work = _abs_dir(
                getattr(config, "YT_PRAYER_WORK_DIR", None), "data/youtube_prayer"
            )
            notes: List[str] = []

            if KIND_HORIZ in kinds:
                notes.append(
                    await self._run_horizontal(
                        count=self._count_for(KIND_HORIZ, counts),
                        chat_id=chat_id,
                        topic_id=topic_id,
                        work=work,
                        history_days=history_days,
                        force=force,
                        progress_chat_id=progress_chat_id,
                    )
                )
            if KIND_SHORTS in kinds:
                notes.append(
                    await self._run_shorts(
                        count=self._count_for(KIND_SHORTS, counts),
                        chat_id=chat_id,
                        topic_id=topic_id,
                        horizontal_work=work,
                        history_days=history_days,
                        force=force,
                        progress_chat_id=progress_chat_id,
                    )
                )
                es_count = int(getattr(config, "YT_SHORTS_ES_COUNT", 0) or 0)
                if es_count > 0:
                    notes.append(
                        await self._run_shorts(
                            count=es_count,
                            chat_id=chat_id,
                            topic_id=topic_id,
                            horizontal_work=None,
                            history_days=history_days,
                            force=force,
                            progress_chat_id=progress_chat_id,
                            lang="es",
                        )
                    )
            await self._say(progress_chat_id, "\n".join(n for n in notes if n))
            # Срез и разбор — после публикации, чтобы вчерашний день был закрыт.
            await self._channel_report(chat_id, topic_id, progress_chat_id)

    async def _run_horizontal(
        self,
        *,
        count: int,
        chat_id: int,
        topic_id: int,
        work: Path,
        history_days: int,
        force: bool,
        progress_chat_id: Optional[int],
    ) -> str:
        if count <= 0:
            logger.info("[%s] горизонтальные пропущены (count=0)", self.name)
            return "Горизонтальные: пропущены (count=0)."
        result = await run_daily_youtube_prayer_pipeline(
            self._app.bot,
            chat_id=chat_id,
            topic_id=topic_id,
            work_root=work,
            count=count,
            force=force,
            progress_chat_id=progress_chat_id,
            history_days=history_days,
            en_enabled=bool(getattr(config, "YT_PRAYER_EN_ENABLED", False)),
            en_count=int(getattr(config, "YT_PRAYER_EN_COUNT", 1) or 1),
            en_voice_id=str(
                getattr(config, "YT_PRAYER_EN_VOICE_ID", None)
                or "a4CnuaYbALRvW39mDitg"
            ),
        )
        if result.skipped and not force:
            return (
                f"Горизонтальные: уже есть прогон за {result.day}. "
                f"Повтор — /yt_prayer force"
            )
        if not result.ok:
            return f"⛔ Горизонтальные остановлены: {result.error}"
        parts = []
        if result.themes:
            parts.append("RU: " + ", ".join(result.themes))
        if result.themes_en:
            parts.append("EN: " + ", ".join(result.themes_en))
        return f"Горизонтальные за {result.day}: " + (" | ".join(parts) or "ok")

    async def _run_shorts(
        self,
        *,
        count: int,
        chat_id: int,
        topic_id: int,
        horizontal_work: Optional[Path],
        history_days: int,
        force: bool,
        progress_chat_id: Optional[int],
        lang: str = "ru",
    ) -> str:
        from youtube_prayer.langs import normalize_lang, profile

        lang = normalize_lang(lang)
        tag = "Shorts" if lang == "ru" else f"Shorts {profile(lang).label}"
        if count <= 0:
            logger.info("[%s] %s пропущены (count=0)", self.name, tag)
            return f"{tag}: пропущены (count=0)."
        from youtube_shorts.pipeline import run_daily_youtube_shorts_pipeline

        # Каждому языку — свой рабочий каталог. Иначе второй прогон за день
        # увидит отметку «готово» от первого и молча ничего не сделает,
        # а истории тем двух каналов перемешаются.
        if lang == "ru":
            shorts_work = _abs_dir(
                getattr(config, "YT_SHORTS_WORK_DIR", None), "data/youtube_shorts"
            )
        else:
            shorts_work = _abs_dir(
                getattr(config, f"YT_SHORTS_WORK_DIR_{lang.upper()}", None),
                f"data/youtube_shorts_{lang}",
            )
        shorts_topic = int(getattr(config, "YT_SHORTS_TOPIC_ID", 0) or 0) or topic_id
        result = await run_daily_youtube_shorts_pipeline(
            self._app.bot,
            chat_id=chat_id,
            topic_id=shorts_topic,
            work_root=shorts_work,
            count=count,
            force=force,
            progress_chat_id=progress_chat_id,
            history_days=history_days,
            horizontal_work_root=horizontal_work,
            lang=lang,
        )
        if result.skipped:
            return f"{tag}: уже готовы за {result.day}. Повтор — /yt_shorts force"
        if not result.ok:
            return f"⛔ {tag} остановлены: {result.error}"
        return f"{tag} за {result.day}: ×{len(result.themes)}"

    async def _channel_report(
        self, chat_id: int, topic_id: int, progress_chat_id: Optional[int]
    ) -> None:
        """Ночной разбор динамики канала. Не роняет прогон: отчёт вторичен."""
        if not getattr(config, "YT_REPORT_ENABLED", True):
            return
        try:
            from youtube_prayer.channel_stats import run_daily_channel_report

            await run_daily_channel_report(
                self._app.bot,
                chat_id=progress_chat_id or chat_id,
                topic_id=0 if progress_chat_id else topic_id,
            )
        except Exception as e:
            logger.warning("[%s] отчёт по каналу не собрался: %s", self.name, e)

    async def _say(self, chat_id: Optional[int], text: str) -> None:
        if not chat_id or not self._app or not text.strip():
            return
        try:
            await self._app.bot.send_message(chat_id, text)
        except Exception:
            pass


def _seconds_until_msk(hour: int, minute: int) -> float:
    now = datetime.now(_MSK)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(5.0, (target - now).total_seconds())
