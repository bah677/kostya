"""Челлендж чтения Писания: /challenge."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.services.scripture_challenge_service import ScriptureChallengeService
from bot.states import ScriptureChallengeStates
from bot.utils.donation_reply import maybe_donation_keyboard
from bot.utils.telegram_html import split_telegram_html_message_chunks
from storage.db.scripture_challenge import ScriptureChallengeMixin, parse_intake_transcript

logger = logging.getLogger(__name__)

_CANCEL_WORDS = frozenset({"отмена", "отменить", "стоп", "cancel", "/cancel"})
_DURATION_PRESETS = (10, 30, 60, 90)
_MSG_CHUNK = 3500

_CHALLENGE_INTRO_HTML = (
    "<b>📖 Челлендж чтения Писания</b>\n\n"
    "<b>Как это работает:</b>\n"
    "1. Короткий диалог — поймём ваш запрос к Писанию\n"
    "2. Выберете срок и удобное время ежедневной рассылки\n"
    "3. Составлю персональный план чтения\n"
    "4. <b>Первый отрывок пришлю сразу</b> после готовности плана\n"
    "5. Со 2-го дня — каждое утро (или в выбранное время) по расписанию\n"
    "6. Можете писать в любой момент — отвечу с учётом плана и нашего диалога\n\n"
    "<b>Отмена:</b> /challenge_cancel или слово «отмена»\n\n"
    "Расскажите: что привело вас к Писанию, что беспокоит "
    "и какой ответ вы ищете."
)


def _duration_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=f"{d} дн.", callback_data=f"challenge_dur_{d}")
            for d in _DURATION_PRESETS[:2]
        ],
        [
            InlineKeyboardButton(text=f"{d} дн.", callback_data=f"challenge_dur_{d}")
            for d in _DURATION_PRESETS[2:]
        ],
        [InlineKeyboardButton(text="✏️ Свой срок (7–365)", callback_data="challenge_dur_custom")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="challenge_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delivery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕘 9:00 МСК", callback_data="challenge_time_9_0_MSK")],
            [InlineKeyboardButton(text="✏️ Другое время", callback_data="challenge_time_custom")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="challenge_cancel")],
        ]
    )


def _parse_duration(text: str) -> Optional[int]:
    m = re.search(r"\d+", (text or "").strip())
    if not m:
        return None
    val = int(m.group())
    if 7 <= val <= 365:
        return val
    return None


def _parse_delivery_time(text: str) -> Optional[tuple[int, int, str]]:
    raw = (text or "").strip().lower()
    tz = "Europe/Moscow"
    if "utc" in raw or "gmt" in raw:
        m_tz = re.search(r"utc\s*([+-]?\d+)", raw)
        if m_tz:
            offset = int(m_tz.group(1))
            tz = f"Etc/GMT{-offset}" if offset != 0 else "UTC"
    elif "мск" in raw or "msk" in raw:
        tz = "Europe/Moscow"
    m = re.search(r"(\d{1,2})[:.](\d{2})", raw)
    if not m:
        m = re.search(r"\b(\d{1,2})\b", raw)
        if m:
            hour = int(m.group(1))
            if 0 <= hour <= 23:
                return hour, 0, tz
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute, tz
    return None


class ScriptureChallengeFeature(BaseFeature):
    name = "scripture_challenge"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot: Optional[Bot] = None
        self.service: Optional[ScriptureChallengeService] = None

    def set_bot(self, app) -> None:
        self.bot = app.bot if app is not None else None

    async def initialize(self) -> None:
        self.service = ScriptureChallengeService(self.user_storage)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(
            self.on_challenge_command,
            Command(commands=["challenge", "chellenge"]),
        )
        dp.message.register(self.on_challenge_cancel_command, Command("challenge_cancel"))
        dp.callback_query.register(
            self.on_callback,
            F.data.startswith("challenge_"),
        )
        logger.info(
            "[%s] /challenge, /challenge_cancel + callback challenge_start",
            self.name,
        )

    async def on_challenge_command(self, message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        await self._start_challenge(message, state, user_id=message.from_user.id)

    async def _start_challenge(
        self, message: Message, state: FSMContext, *, user_id: int
    ) -> None:
        active = await self.user_storage.get_user_active_scripture_challenge(user_id)
        if active:
            status = active.get("status")
            if status == "active":
                await message.answer(
                    "У вас уже идёт челлендж чтения Писания.\n"
                    "Пишите сюда — я помню контекст. Завершить: /challenge_cancel",
                    parse_mode=ParseMode.HTML,
                )
                return
            await state.set_state(
                ScriptureChallengeStates.planning
                if status == "planning"
                else ScriptureChallengeStates.intake
            )
            await state.update_data(challenge_id=active["id"])
            await message.answer(
                "Продолжаем настройку челленджа. Напишите ответ или /challenge_cancel."
            )
            return

        challenge_id = await self.user_storage.create_scripture_challenge(user_id)
        if not challenge_id:
            await message.answer("Не удалось начать челлендж. Попробуйте позже.")
            return

        await state.set_state(ScriptureChallengeStates.intake)
        await state.update_data(challenge_id=challenge_id)

        await message.answer(_CHALLENGE_INTRO_HTML, parse_mode=ParseMode.HTML)

    async def on_challenge_cancel_command(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        challenge_id = data.get("challenge_id")
        if not challenge_id:
            ch = await self.user_storage.get_user_active_scripture_challenge(message.from_user.id)
            challenge_id = ch["id"] if ch else None
        if challenge_id:
            await self.user_storage.cancel_scripture_challenge(challenge_id)
        await state.clear()
        await message.answer("Челлендж отменён. Когда будете готовы — снова /challenge")

    async def on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        await callback.answer()

        # Кнопка рассылки → тот же старт, что /challenge
        if data == "challenge_start":
            if callback.message and callback.from_user:
                await self._start_challenge(
                    callback.message, state, user_id=callback.from_user.id
                )
            return

        if data == "challenge_cancel":
            await self.on_challenge_cancel_command(callback.message, state)
            return

        cur = await state.get_state()
        fsm = await state.get_data()
        challenge_id = fsm.get("challenge_id")

        if data.startswith("challenge_dur_"):
            if data == "challenge_dur_custom":
                await state.set_state(ScriptureChallengeStates.duration)
                await callback.message.answer(
                    "Введите число дней (от 7 до 365), например: <code>45</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            days = int(data.rsplit("_", 1)[-1])
            await state.update_data(duration_days=days)
            await self._ask_delivery_time(callback.message, state)
            return

        if data.startswith("challenge_time_"):
            if data == "challenge_time_custom":
                await state.set_state(ScriptureChallengeStates.delivery_time)
                await callback.message.answer(
                    "Во сколько присылать отрывок? Например: <code>9:00</code> или "
                    "<code>8:30 МСК</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            parts = data.split("_")
            hour, minute = int(parts[2]), int(parts[3])
            tz = "Europe/Moscow" if parts[4] == "MSK" else parts[4]
            await state.update_data(
                delivery_hour=hour, delivery_minute=minute, delivery_tz=tz
            )
            await self._start_planning(callback.message, state, challenge_id)
            return

        logger.warning("[%s] unhandled callback %s state=%s", self.name, data, cur)

    async def handle_message(self, message: Message, state: FSMContext, text: str) -> None:
        cur = await state.get_state()
        content = (text or "").strip()
        data = await state.get_data()
        challenge_id = data.get("challenge_id")

        if not cur or "ScriptureChallengeStates" not in str(cur):
            ch = await self.user_storage.get_user_active_scripture_challenge(
                message.from_user.id
            )
            if not ch:
                return
            challenge_id = ch["id"]
            status = ch.get("status")
            if status == "active":
                await self._handle_active_chat(message, ch, text)
                return
            if status == "intake":
                await state.set_state(ScriptureChallengeStates.intake)
                await state.update_data(challenge_id=challenge_id)
            elif status == "planning":
                await message.answer("План чтения составляется, подождите немного…")
                return
            else:
                return
            cur = await state.get_state()

        if content.lower() in _CANCEL_WORDS:
            await self.on_challenge_cancel_command(message, state)
            return

        if not challenge_id:
            ch = await self.user_storage.get_user_active_scripture_challenge(
                message.from_user.id
            )
            challenge_id = ch["id"] if ch else None
        if not challenge_id:
            await message.answer("Сессия челленджа не найдена. Начните с /challenge")
            await state.clear()
            return

        if cur and cur.endswith("intake"):
            await self._handle_intake(message, state, challenge_id, content)
        elif cur and cur.endswith("duration"):
            days = _parse_duration(content)
            if not days:
                await message.answer("Укажите число от 7 до 365.")
                return
            await state.update_data(duration_days=days)
            await self._ask_delivery_time(message, state)
        elif cur.endswith("delivery_time"):
            parsed = _parse_delivery_time(content)
            if not parsed:
                await message.answer("Не понял время. Пример: 9:00 или 8:30 МСК")
                return
            hour, minute, tz = parsed
            await state.update_data(
                delivery_hour=hour, delivery_minute=minute, delivery_tz=tz
            )
            await self._start_planning(message, state, challenge_id)
        elif cur.endswith("planning"):
            await message.answer("План чтения составляется, подождите немного…")

    async def _handle_intake(
        self, message: Message, state: FSMContext, challenge_id: int, content: str
    ) -> None:
        if not content:
            await message.answer("Напишите ответ текстом или «отмена».")
            return

        await self.user_storage.append_intake_message(challenge_id, "user", content)

        ch = await self.user_storage.get_scripture_challenge(challenge_id)
        transcript = parse_intake_transcript(ch.get("intake_transcript") if ch else None)
        logger.info(
            "[%s] intake challenge=%s turns=%s",
            self.name,
            challenge_id,
            len(transcript),
        )

        reply, summary, canceled = await self.service.intake_reply(
            user_id=message.from_user.id,
            transcript=transcript,
        )
        if canceled:
            await self.on_challenge_cancel_command(message, state)
            return

        if reply:
            await self.user_storage.append_intake_message(challenge_id, "assistant", reply)
            await message.answer(reply)

        if summary:
            await self.user_storage.update_scripture_challenge(
                challenge_id, user_request_summary=summary
            )
            await state.set_state(ScriptureChallengeStates.duration)
            await message.answer(
                "Спасибо. Выберите срок челленджа:",
                reply_markup=_duration_keyboard(),
            )

    async def _ask_delivery_time(self, message: Message, state: FSMContext) -> None:
        await state.set_state(ScriptureChallengeStates.delivery_time)
        await message.answer(
            "В какое время присылать ежедневный отрывок?",
            reply_markup=_delivery_keyboard(),
        )

    async def _start_planning(
        self, message: Message, state: FSMContext, challenge_id: int
    ) -> None:
        data = await state.get_data()
        duration = int(data["duration_days"])
        hour = int(data.get("delivery_hour", 9))
        minute = int(data.get("delivery_minute", 0))
        tz = data.get("delivery_tz", "Europe/Moscow")

        await state.set_state(ScriptureChallengeStates.planning)
        wait = await message.answer("⏳ Составляю персональный план чтения Писания…")

        ch = await self.user_storage.get_scripture_challenge(challenge_id)
        summary = ch.get("user_request_summary") or ""
        transcript = parse_intake_transcript(ch.get("intake_transcript") if ch else None)

        await self.user_storage.update_scripture_challenge(
            challenge_id,
            status="planning",
            duration_days=duration,
            delivery_hour=hour,
            delivery_minute=minute,
            delivery_tz=tz,
        )

        plan_items = await self.service.build_plan_with_review(
            user_id=message.from_user.id,
            summary=summary,
            duration_days=duration,
            intake_transcript=transcript,
        )
        if not plan_items or len(plan_items) < 1:
            await wait.edit_text(
                "Не удалось составить план. Попробуйте /challenge ещё раз позже."
            )
            await self.user_storage.cancel_scripture_challenge(challenge_id)
            await state.clear()
            return

        if len(plan_items) != duration:
            for i, it in enumerate(plan_items[:duration], start=1):
                it["day_number"] = i
            plan_items = plan_items[:duration]

        await self.user_storage.replace_plan_items(challenge_id, plan_items)

        now = datetime.now(timezone.utc)
        next_weekly = now + timedelta(days=7)

        await self.user_storage.update_scripture_challenge(
            challenge_id,
            status="active",
            current_day=0,
            started_at=now,
            next_delivery_at=None,
            next_weekly_review_at=next_weekly,
        )

        challenge = await self.user_storage.get_scripture_challenge(challenge_id)
        day1_ok = False
        if challenge:
            try:
                await self.send_daily_passage(challenge)
                day1_ok = True
            except Exception as e:
                logger.error(
                    "[%s] immediate day 1 challenge=%s: %s",
                    self.name,
                    challenge_id,
                    e,
                    exc_info=True,
                )

        challenge = await self.user_storage.get_scripture_challenge(challenge_id)
        await state.clear()

        if not day1_ok:
            await wait.edit_text(
                "План готов, но не удалось отправить первый отрывок. "
                "Он придёт по расписанию или напишите /challenge_cancel и начните снова."
            )
            if challenge and challenge.get("current_day", 0) == 0:
                next_delivery = ScriptureChallengeMixin.compute_next_delivery_at(
                    hour=hour, minute=minute, tz_name=tz, after=now
                )
                await self.user_storage.update_scripture_challenge(
                    challenge_id, next_delivery_at=next_delivery
                )
            return

        next_hint = ""
        if duration > 1 and challenge:
            nd = challenge.get("next_delivery_at")
            if nd:
                local_nd = nd.astimezone(ZoneInfo(tz))
                next_hint = (
                    f"\n\nСо 2-го дня отрывки будут приходить в "
                    f"<b>{hour:02d}:{minute:02d}</b> ({tz}). "
                    f"Ближайшая отправка: {local_nd.strftime('%d.%m в %H:%M')}."
                )

        await wait.edit_text(
            f"<b>✅ Челлендж на {duration} дн. начался</b>\n\n"
            f"Первый отрывок отправлен отдельным сообщением.{next_hint}\n\n"
            "Пишите в любое время — я помню ваш запрос и план чтения.\n"
            "Отменить: /challenge_cancel",
            parse_mode=ParseMode.HTML,
        )

    async def _handle_active_chat(
        self, message: Message, challenge: Dict[str, Any], text: str
    ) -> None:
        if not text.strip():
            return
        cid = challenge["id"]
        await self.user_storage.add_challenge_message(cid, "user", text)

        plan = await self.user_storage.get_plan_items(cid)
        plan_summary = "; ".join(
            f"д{it['day_number']}: {it['reference']}" for it in plan[:5]
        )
        if len(plan) > 5:
            plan_summary += "…"

        recent = await self.user_storage.get_challenge_messages(cid, limit=14)
        reply = await self.service.challenge_chat_reply(
            user_id=message.from_user.id,
            summary=challenge.get("user_request_summary") or "",
            plan_summary=plan_summary,
            current_day=int(challenge.get("current_day") or 0),
            recent_messages=recent,
        )
        if not reply:
            await message.answer("Сейчас не получилось ответить. Попробуйте чуть позже.")
            return
        await self.user_storage.add_challenge_message(cid, "assistant", reply)
        keyboard, _ = await maybe_donation_keyboard(self.user_storage, message.from_user.id)
        await message.answer(reply, reply_markup=keyboard)

    async def send_daily_passage(self, challenge: Dict[str, Any]) -> None:
        if not self.bot:
            return
        cid = challenge["id"]
        user_id = challenge["user_id"]
        day = int(challenge.get("current_day") or 0) + 1
        duration = int(challenge.get("duration_days") or 0)

        if day > duration:
            await self.user_storage.update_scripture_challenge(
                cid,
                status="completed",
                completed_at=datetime.now(timezone.utc),
                next_delivery_at=None,
                next_weekly_review_at=None,
            )
            await self.bot.send_message(
                user_id,
                "🎉 Вы завершили челлендж чтения Писания! "
                "Благословенного пути дальше. Новый челлендж — /challenge",
            )
            return

        item = await self.user_storage.get_plan_item_for_day(cid, day)
        if not item:
            logger.error("[%s] no plan item challenge=%s day=%s", self.name, cid, day)
            return

        ref = html.escape(item["reference"])
        passage = html.escape(item["passage_text"])
        body = f"<b>📖 День {day} из {duration}</b>\n<b>{ref}</b>\n\n<blockquote>{passage}</blockquote>"

        plan = await self.user_storage.get_plan_items(cid)
        plan_excerpt = "; ".join(
            f"д{it['day_number']}: {it['reference']}"
            for it in plan
            if int(it["day_number"]) <= day + 2
        )
        recent = await self.user_storage.get_challenge_messages(cid, limit=10)
        dialog = "\n".join(f"{m['role']}: {m['content']}" for m in recent[-6:])

        comment = await self.service.daily_comment(
            user_id=user_id,
            summary=challenge.get("user_request_summary") or "",
            plan_excerpt=plan_excerpt,
            recent_dialog=dialog,
            today_reference=item["reference"],
            today_passage=item["passage_text"],
        )
        if comment:
            body += f"\n\n{html.escape(comment)}"
        body += "\n\n<i>Напишите мысли или вопрос — продолжим диалог.</i>"

        chunks = split_telegram_html_message_chunks(body, max_len=_MSG_CHUNK) or [body]
        keyboard, _ = await maybe_donation_keyboard(self.user_storage, user_id)
        for idx, chunk in enumerate(chunks):
            await self.bot.send_message(
                user_id,
                chunk,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard if idx == len(chunks) - 1 else None,
            )

        await self.user_storage.mark_plan_item_sent(item["id"])
        await self.user_storage.add_challenge_message(
            cid, "assistant", f"[День {day}] {item['reference']}\n{item['passage_text']}"
        )
        if comment:
            await self.user_storage.add_challenge_message(cid, "assistant", comment)

        hour = int(challenge.get("delivery_hour") or 9)
        minute = int(challenge.get("delivery_minute") or 0)
        tz = challenge.get("delivery_tz") or "Europe/Moscow"
        now = datetime.now(timezone.utc)
        next_delivery = ScriptureChallengeMixin.compute_next_delivery_at(
            hour=hour, minute=minute, tz_name=tz, after=now
        )

        fields: Dict[str, Any] = {
            "current_day": day,
            "last_daily_sent_at": now,
            "next_delivery_at": next_delivery if day < duration else None,
        }
        if day >= duration:
            fields["status"] = "completed"
            fields["completed_at"] = now
            fields["next_weekly_review_at"] = None
        await self.user_storage.update_scripture_challenge(cid, **fields)

    async def run_weekly_review(self, challenge: Dict[str, Any]) -> None:
        cid = challenge["id"]
        user_id = challenge["user_id"]
        current_day = int(challenge.get("current_day") or 0)
        duration = int(challenge.get("duration_days") or 0)
        if current_day >= duration:
            return

        plan = await self.user_storage.get_plan_items(cid)
        depth = min(14, max(1, current_day))
        recent = await self.user_storage.get_challenge_messages(cid, limit=depth * 2)

        updates = await self.service.weekly_review_plan(
            user_id=user_id,
            summary=challenge.get("user_request_summary") or "",
            current_day=current_day + 1,
            duration_days=duration,
            plan_items=plan,
            recent_messages=recent,
        )
        if updates:
            await self.user_storage.patch_plan_items(cid, updates)

        now = datetime.now(timezone.utc)
        await self.user_storage.update_scripture_challenge(
            cid,
            last_weekly_review_at=now,
            next_weekly_review_at=now + timedelta(days=7),
        )
