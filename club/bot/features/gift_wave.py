"""Подарочная волна: планировщик партий, /wave, встречающие."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.club_greeter_service import (
    pause_greeter_for_today,
    process_greeter_timeouts,
)
from bot.services.gift_wave_service import grant_wave_batch
from bot.texts import ru_gift_wave as txt
from config import config

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _parse_user_ids(raw: str) -> List[int]:
    ids: List[int] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


class GiftWaveFeature(BaseFeature):
    name = "gift_wave"

    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self._scheduler: Optional[AsyncIOScheduler] = None

    def register_handlers(self, dp: Dispatcher) -> None:
        admin_private = F.chat.type == ChatType.PRIVATE
        dp.message.register(self._cmd_wave, admin_private, Command("wave"))
        dp.callback_query.register(
            self._cb_greeter_pause, F.data.startswith("gw:pause:")
        )
        dp.callback_query.register(
            self._cb_greeter_leave, F.data.startswith("gw:leave:")
        )
        dp.callback_query.register(
            self._cb_greeter_invite_yes, F.data == "gw:invite:yes"
        )
        dp.callback_query.register(
            self._cb_greeter_invite_no, F.data == "gw:invite:no"
        )

    async def initialize(self) -> None:
        await super().initialize()
        if not getattr(config, "GIFT_WAVE_ENABLED", True):
            logger.info("[%s] disabled", self.name)
            return
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._scheduler.add_job(
            self._hourly_tick,
            IntervalTrigger(hours=1),
            id="gift_wave_hourly",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.add_job(
            self._greeter_tick,
            IntervalTrigger(minutes=5),
            id="gift_wave_greeter",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info("[%s] scheduler started", self.name)

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    async def _hourly_tick(self) -> None:
        waves = await self.user_storage.list_running_gift_waves()
        for w in waves:
            try:
                result = await grant_wave_batch(
                    user_storage=self.user_storage,
                    bot=self.bot,
                    feature_manager=self.feature_manager,
                    wave_id=int(w["id"]),
                )
                logger.info("[%s] wave %s tick: %s", self.name, w["id"], result)
            except Exception as e:
                logger.exception("[%s] wave %s failed: %s", self.name, w["id"], e)

    async def _greeter_tick(self) -> None:
        try:
            await process_greeter_timeouts(
                user_storage=self.user_storage, bot=self.bot
            )
        except Exception as e:
            logger.exception("[%s] greeter tick: %s", self.name, e)

    async def _cmd_wave(self, message: Message, command: CommandObject) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            return
        args = (command.args or "").strip()
        if not args:
            await message.answer(await self._status_all_html(), parse_mode=ParseMode.HTML)
            return

        parts = args.split(maxsplit=2)
        action = parts[0].lower()

        if action == "new" and len(parts) >= 2:
            # /wave new Название [| batch=25 interval=48 days=30]
            rest = args[4:].strip()
            title = rest
            batch, interval, days = 25, 48, int(getattr(config, "GIFT_WAVE_LICENSE_DAYS", 30))
            if "|" in rest:
                title, opts = rest.split("|", 1)
                title = title.strip()
                for tok in opts.split():
                    if tok.startswith("batch="):
                        batch = max(1, min(25, int(tok.split("=", 1)[1])))
                    elif tok.startswith("interval="):
                        interval = max(1, int(tok.split("=", 1)[1]))
                    elif tok.startswith("days="):
                        days = max(1, int(tok.split("=", 1)[1]))
            wid = await self.user_storage.create_gift_wave(
                title=title or "Волна",
                batch_size=batch,
                interval_hours=interval,
                gift_days=days,
            )
            await message.answer(
                f"✅ Волна <b>{wid}</b> «{title}» создана (draft).\n"
                f"Добавь участников: <code>/wave add {wid} id1 id2 …</code>\n"
                f"Или файл: ответь на .txt со списком id командой "
                f"<code>/wave addfile {wid}</code>\n"
                f"Старт: <code>/wave start {wid}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "add" and len(parts) >= 3:
            wid = int(parts[1])
            ids = _parse_user_ids(parts[2])
            n = await self.user_storage.enqueue_gift_wave_members(wid, ids)
            await message.answer(f"✅ В волну {wid} добавлено: {n} (из {len(ids)})")
            return

        if action == "addfile" and len(parts) >= 2:
            wid = int(parts[1])
            doc = message.reply_to_message.document if message.reply_to_message else None
            if not doc:
                await message.answer("Ответь командой на файл .txt со списком user_id")
                return
            dest = Path(f"/tmp/wave_{wid}_{doc.file_id}.txt")
            await self.bot.download(doc, destination=dest)
            raw = dest.read_text(encoding="utf-8", errors="ignore")
            ids = _parse_user_ids(raw)
            n = await self.user_storage.enqueue_gift_wave_members(wid, ids)
            await message.answer(f"✅ Из файла в волну {wid}: {n} (из {len(ids)})")
            return

        if action in ("start", "pause", "stop") and len(parts) >= 2:
            wid = int(parts[1])
            status = {"start": "running", "pause": "paused", "stop": "done"}[action]
            ok = await self.user_storage.set_gift_wave_status(wid, status)
            if ok and action == "start":
                # сразу попробовать первую партию
                result = await grant_wave_batch(
                    user_storage=self.user_storage,
                    bot=self.bot,
                    feature_manager=self.feature_manager,
                    wave_id=wid,
                )
                await message.answer(
                    f"✅ Волна {wid} → <b>{status}</b>\nПервая партия: {result}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await message.answer(f"{'✅' if ok else '❌'} Волна {wid} → {status}")
            return

        if action == "status":
            wid = int(parts[1]) if len(parts) >= 2 else 0
            if wid:
                await message.answer(
                    await self._status_one_html(wid), parse_mode=ParseMode.HTML
                )
            else:
                await message.answer(
                    await self._status_all_html(), parse_mode=ParseMode.HTML
                )
            return

        if action == "faster" and len(parts) >= 2:
            wid = int(parts[1])
            w = await self.user_storage.get_gift_wave(wid)
            if not w:
                await message.answer("Нет волны")
                return
            ih = max(12, int(w["interval_hours"]) // 2)
            await self.user_storage.update_gift_wave_timing(wid, interval_hours=ih)
            await message.answer(f"✅ Интервал волны {wid}: {ih} ч")
            return

        if action == "slower" and len(parts) >= 2:
            wid = int(parts[1])
            w = await self.user_storage.get_gift_wave(wid)
            if not w:
                await message.answer("Нет волны")
                return
            ih = min(240, int(w["interval_hours"]) * 2)
            await self.user_storage.update_gift_wave_timing(wid, interval_hours=ih)
            await message.answer(f"✅ Интервал волны {wid}: {ih} ч")
            return

        if action == "greeter":
            sub = parts[1].lower() if len(parts) >= 2 else "list"
            if sub == "list":
                rows = await self.user_storage.list_club_greeters()
                if not rows:
                    await message.answer("Пул встречающих пуст.")
                    return
                lines = []
                for r in rows:
                    mark = "✅" if r["active"] else "⏸"
                    un = r.get("username") or r.get("first_name") or ""
                    lines.append(
                        f"{mark} <code>{r['user_id']}</code> {un} "
                        f"cap={r['capacity']} miss={r['miss_streak']}"
                    )
                await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
                return
            if sub == "sync":
                from bot.services.club_greeter_service import sync_greeter_pool_licenses

                removed = await sync_greeter_pool_licenses(
                    user_storage=self.user_storage, bot=self.bot
                )
                await message.answer(
                    f"Снято без лицензии: {len(removed)}"
                    + (f"\n<code>{removed}</code>" if removed else "")
                )
                return
            if sub in ("room", "room-sync"):
                from bot.services.greeter_room_service import (
                    greeter_chat_configured,
                    sync_greeter_chat_invites,
                )

                if not greeter_chat_configured():
                    await message.answer("Чат встречающих не настроен (GREETER_CHAT_*).")
                    return
                await message.answer("Рассылаю инвайты в чат команды…")
                stats = await sync_greeter_chat_invites(
                    self.bot, self.user_storage, include_admins=True
                )
                await message.answer(
                    f"Чат встречающих: ok={stats.get('ok')} "
                    f"fail={stats.get('fail')} total={stats.get('total')}"
                )
                return
            if sub == "suggest":
                from bot.services.club_greeter_service import (
                    fetch_greeter_pool_candidates,
                )

                cands = await fetch_greeter_pool_candidates(
                    self.user_storage, limit=15
                )
                if not cands:
                    await message.answer("Подходящих кандидатов не нашёл.")
                    return
                lines = ["<b>Кандидаты в пул встречающих</b> (лицензия + в группе):"]
                for r in cands:
                    un = r.get("username") or r.get("first_name") or "—"
                    lines.append(
                        f"• <code>{r['user_id']}</code> @{un} — "
                        f"ответов≈{r['replies_180d']}, за 30д={r['msgs_30d']}"
                    )
                lines.append(
                    "\nДобавить: <code>/wave greeter add UID</code>"
                )
                await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
                return
            if sub == "add" and len(parts) >= 3:
                from bot.services.greeter_room_service import on_greeter_activated

                gid = int(parts[2].split()[0])
                ok = await self.user_storage.upsert_club_greeter(gid, active=True)
                if ok:
                    await on_greeter_activated(self.bot, self.user_storage, gid)
                await message.answer(f"{'✅' if ok else '❌'} greeter {gid}")
                return
            if sub == "remove" and len(parts) >= 3:
                from bot.services.greeter_room_service import on_greeter_deactivated

                gid = int(parts[2].split()[0])
                ok = await self.user_storage.set_greeter_active(gid, False)
                if ok:
                    await on_greeter_deactivated(self.bot, self.user_storage, gid)
                await message.answer(f"{'✅' if ok else '❌'} greeter {gid} off")
                return

        await message.answer(
            "<b>/wave</b> — сводка\n"
            "<code>/wave new Название | batch=25 interval=48 days=30</code>\n"
            "<code>/wave add ID uid1 uid2…</code>\n"
            "<code>/wave addfile ID</code> (reply на txt)\n"
            "<code>/wave start|pause|stop ID</code>\n"
            "<code>/wave status [ID]</code>\n"
            "<code>/wave faster|slower ID</code>\n"
            "<code>/wave greeter list|suggest|sync|room|add UID|remove UID</code>",
            parse_mode=ParseMode.HTML,
        )

    async def _status_one_html(self, wave_id: int) -> str:
        w = await self.user_storage.get_gift_wave(wave_id)
        if not w:
            return "Волна не найдена"
        c = await self.user_storage.wave_counts(wave_id)
        granted = (
            c.get("granted", 0)
            + c.get("joined", 0)
            + c.get("spoke", 0)
            + c.get("declined", 0)
        )
        joined = c.get("joined", 0) + c.get("spoke", 0)
        spoke = c.get("spoke", 0)
        total = c.get("total", 0)
        return (
            f"<b>Волна «{w['title']}»</b> — <code>{w['status']}</code> (id={wave_id})\n"
            f"Выдано: {granted} / {total}\n"
            f"Вошли: {joined}\n"
            f"Заговорили: {spoke}\n"
            f"Партия: {w['batch_size']}, интервал: {w['interval_hours']} ч, "
            f"подарок: {w['gift_days']} дн.\n"
            f"Последняя партия: {w.get('last_batch_at') or '—'}\n"
            f"{w.get('paused_reason') or ''}"
        )

    async def _status_all_html(self) -> str:
        waves = await self.user_storage.list_gift_waves(limit=10)
        if not waves:
            return "Волн пока нет. <code>/wave new …</code>"
        chunks = [await self._status_one_html(int(w["id"])) for w in waves]
        return "\n\n".join(chunks)

    async def _cb_greeter_pause(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        await pause_greeter_for_today(self.user_storage, uid)
        await callback.answer("Сегодня не назначаю")
        try:
            await callback.message.answer(txt.GREETER_PAUSED_TODAY_HTML)
        except Exception:
            pass

    async def _cb_greeter_leave(self, callback: CallbackQuery) -> None:
        from bot.services.greeter_room_service import on_greeter_deactivated

        uid = callback.from_user.id if callback.from_user else 0
        await self.user_storage.set_greeter_active(uid, False)
        await on_greeter_deactivated(self.bot, self.user_storage, uid)
        await callback.answer("Снял с пула")
        try:
            await callback.message.answer(txt.GREETER_LEFT_POOL_HTML)
        except Exception:
            pass

    async def _cb_greeter_invite_yes(self, callback: CallbackQuery) -> None:
        from bot.services.greeter_room_service import on_greeter_activated

        uid = callback.from_user.id if callback.from_user else 0
        await self.user_storage.upsert_club_greeter(uid, active=True, capacity=3)
        await on_greeter_activated(self.bot, self.user_storage, uid)
        await callback.answer("Спасибо!")
        try:
            await callback.message.answer(txt.GREETER_INVITE_ACCEPTED_HTML)
        except Exception:
            pass

    async def _cb_greeter_invite_no(self, callback: CallbackQuery) -> None:
        from bot.services.greeter_room_service import on_greeter_deactivated

        uid = callback.from_user.id if callback.from_user else 0
        await self.user_storage.set_greeter_active(uid, False)
        await on_greeter_deactivated(self.bot, self.user_storage, uid)
        await callback.answer("Хорошо")
        try:
            await callback.message.answer(txt.GREETER_INVITE_DECLINED_HTML)
        except Exception:
            pass
