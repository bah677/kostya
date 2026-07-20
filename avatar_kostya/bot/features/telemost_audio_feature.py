"""Кнопка и текстовые команды нарезки аудио / полной записи в топике шортсов."""

from __future__ import annotations

import logging
import re
from typing import Any

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.utils.rag_admin_context import is_rag_shorts_message, rag_shorts_chat_topic
from config import config
from telemost_audio.full_voice_pipeline import enqueue_telemost_full_voice_by_meeting_id
from telemost_audio.pipeline import (
    enqueue_telemost_audio_by_meeting_id,
    enqueue_telemost_audio_last,
)

logger = logging.getLogger(__name__)

CB_CUT_AUDIO = "ts:cut_audio"
CB_CUT_LAST = "ts:cut_last"  # legacy pinned button from video shorts panel

# «нарезать шортцы 3057900798» / «нарезать шортсы №3057900798»
_RE_CUT_SHORTS = re.compile(
    r"(?is)^\s*нарезать\s+шорт[сц]ы?\s*(?:встречи\s*)?(?:№\s*)?(\d{6,})\s*$"
)
# «выложить полную запись встречи 3057900798»
_RE_FULL_VOICE = re.compile(
    r"(?is)^\s*выложить\s+полную\s+запись\s*(?:встречи\s*)?(?:№\s*)?(\d{6,})\s*$"
)


class TelemostAudioFeature(BaseFeature):
    name = "telemost_audio"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def _cut_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎙 Новая аудио-нарезка",
                        callback_data=CB_CUT_AUDIO,
                    )
                ]
            ]
        )

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        # Команды по номеру встречи — даже если клипы выключены (полная запись).
        dispatcher.message.register(
            self.cmd_phrase_or_slash,
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
            F.text.regexp(_RE_CUT_SHORTS),
        )
        dispatcher.message.register(
            self.cmd_phrase_or_slash,
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
            F.text.regexp(_RE_FULL_VOICE),
        )
        dispatcher.message.register(
            self.cmd_audio_cut_args,
            Command("audio_cut"),
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        )
        dispatcher.message.register(
            self.cmd_full_voice,
            Command("full_voice"),
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        )

        if not config.TELEMOST_AUDIO_CLIPS_ENABLED:
            self.log("TELEMOST_AUDIO_CLIPS_ENABLED=0 — кнопка нарезки не регистрируется")
            return
        dispatcher.callback_query.register(
            self.on_cut_callback,
            F.data.in_({CB_CUT_AUDIO, CB_CUT_LAST}),
        )
        if not getattr(config, "TELEMOST_VIDEO_SHORTS_ENABLED", False):
            dispatcher.message.register(
                self.cmd_panel,
                Command("shorts_cut"),
                F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
            )

    async def _ensure_shorts_admin(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return False
        if not is_rag_shorts_message(message.chat.id, message.message_thread_id):
            _, topic_id = rag_shorts_chat_topic()
            await message.reply(
                f"Команда только в топике шортсов (ветка <code>{topic_id}</code>).",
                parse_mode=ParseMode.HTML,
            )
            return False
        return True

    async def _run_cut(self, answer_fn) -> None:
        if self._app is None:
            await answer_fn("Бот не инициализирован", alert=True)
            return
        ok, msg = await enqueue_telemost_audio_last(
            self._app, force=True, regenerate_moments=True
        )
        if ok:
            await answer_fn(
                f"Новая аудио-нарезка: {msg[:100]}",
                alert=False,
            )
        else:
            await answer_fn(msg, alert=True)

    async def on_cut_callback(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только для администратора.", show_alert=True)
            return
        msg = callback.message
        if msg and not is_rag_shorts_message(msg.chat.id, msg.message_thread_id):
            await callback.answer("Кнопка только в топике шортсов.", show_alert=True)
            return

        async def answer(text: str, *, alert: bool) -> None:
            await callback.answer(text, show_alert=alert)

        await self._run_cut(answer)

    async def cmd_panel(self, message: Message) -> None:
        if not await self._ensure_shorts_admin(message):
            return
        await message.answer(
            "🎙 Мини-подкасты ~1 мин → голосовые в Telegram.\n"
            "Каждое нажатие — новые фрагменты.\n\n"
            "Или текстом:\n"
            "<code>нарезать шортцы 1234567890</code>\n"
            "<code>выложить полную запись встречи 1234567890</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=self._cut_keyboard(),
        )

    async def cmd_phrase_or_slash(self, message: Message) -> None:
        if not await self._ensure_shorts_admin(message):
            return
        if self._app is None:
            await message.answer("Бот не инициализирован")
            return
        text = (message.text or "").strip()
        m_cut = _RE_CUT_SHORTS.match(text)
        if m_cut:
            mid = m_cut.group(1)
            if not getattr(config, "TELEMOST_AUDIO_CLIPS_ENABLED", False):
                await message.answer("Аудио-нарезка выключена (TELEMOST_AUDIO_CLIPS_ENABLED=0).")
                return
            ok, msg = await enqueue_telemost_audio_by_meeting_id(
                self._app, mid, force=True, regenerate_moments=True
            )
            await message.answer(
                f"{'✅ Запустил нарезку' if ok else '⚠️'} {msg}",
                parse_mode=ParseMode.HTML,
            )
            return
        m_full = _RE_FULL_VOICE.match(text)
        if m_full:
            mid = m_full.group(1)
            ok, msg = await enqueue_telemost_full_voice_by_meeting_id(
                self._app, mid, force=True
            )
            await message.answer(
                f"{'✅ Выкладываю полную запись' if ok else '⚠️'} {msg}",
                parse_mode=ParseMode.HTML,
            )

    async def cmd_audio_cut_args(self, message: Message) -> None:
        if not await self._ensure_shorts_admin(message):
            return
        if self._app is None:
            await message.answer("Бот не инициализирован")
            return
        parts = (message.text or "").split(maxsplit=1)
        arg = (parts[1] if len(parts) > 1 else "").strip()
        digits = re.sub(r"\D", "", arg)
        if digits:
            if not getattr(config, "TELEMOST_AUDIO_CLIPS_ENABLED", False):
                await message.answer("Аудио-нарезка выключена.")
                return
            ok, msg = await enqueue_telemost_audio_by_meeting_id(
                self._app, digits, force=True, regenerate_moments=True
            )
            await message.answer(
                f"{'✅ Запустил нарезку' if ok else '⚠️'} {msg}",
                parse_mode=ParseMode.HTML,
            )
            return
        # без номера — панель / последняя встреча
        if not getattr(config, "TELEMOST_AUDIO_CLIPS_ENABLED", False):
            await message.answer(
                "Укажите номер: <code>/audio_cut 1234567890</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await self.cmd_panel(message)

    async def cmd_full_voice(self, message: Message) -> None:
        if not await self._ensure_shorts_admin(message):
            return
        if self._app is None:
            await message.answer("Бот не инициализирован")
            return
        parts = (message.text or "").split(maxsplit=1)
        arg = (parts[1] if len(parts) > 1 else "").strip()
        digits = re.sub(r"\D", "", arg)
        if not digits:
            await message.answer(
                "Укажите номер встречи:\n"
                "<code>/full_voice 1234567890</code>\n"
                "или: <code>выложить полную запись встречи 1234567890</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        ok, msg = await enqueue_telemost_full_voice_by_meeting_id(
            self._app, digits, force=True
        )
        await message.answer(
            f"{'✅ Выкладываю полную запись' if ok else '⚠️'} {msg}",
            parse_mode=ParseMode.HTML,
        )
