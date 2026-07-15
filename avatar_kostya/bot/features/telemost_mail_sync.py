"""Мониторинг почты Телемоста → подтверждение в группе → RAG."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from html import escape as html_escape
from typing import Any, Optional

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
from bot.filters.private_only import PRIVATE_CHAT
from bot.utils.rag_admin_context import rag_admin_chat_topic, is_rag_admin_message
from config import config
from telemost_mail.classifier_llm import TelemostClassification
from telemost_mail.decisions import resolve_mail_decision
from telemost_mail.service import TelemostMailService

logger = logging.getLogger(__name__)

CB_PREFIX = "tm_rag:"
CB_LOAD = f"{CB_PREFIX}load:"
CB_IGNORE = f"{CB_PREFIX}ignore:"
CB_KIND = f"{CB_PREFIX}kind:"

KIND_EFIR = "efir"
KIND_MOLITVA = "molitva"
KIND_OTHER = "other"


class TelemostMailFeature(BaseFeature):
    name = "telemost_mail"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._task: Optional[asyncio.Task] = None
        self._poll_lock = asyncio.Lock()

    def set_bot(self, app: Any) -> None:
        self._app = app

    def _service(self) -> Optional[TelemostMailService]:
        if self._app is None:
            return None
        rs = getattr(self._app, "rag_stack", None)
        svc = TelemostMailService.from_config(
            config,
            user_storage=self._app.user_storage,
            material_index=rs.materials if rs else None,
        )
        svc.set_bot_app(self._app)
        return svc

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        if not config.TELEMOST_MAIL_ENABLED:
            self.log("TELEMOST_MAIL_ENABLED=0 — хендлеры не регистрируются")
            return
        dispatcher.message.register(
            self.cmd_telemost_status, PRIVATE_CHAT, Command("telemost_status")
        )
        dispatcher.message.register(
            self.cmd_telemost_poll, PRIVATE_CHAT, Command("telemost_poll")
        )
        dispatcher.message.register(
            self.cmd_telemost_load,
            Command("telemost_load"),
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        )
        dispatcher.message.register(
            self.cmd_telemost_load, PRIVATE_CHAT, Command("telemost_load")
        )
        dispatcher.callback_query.register(
            self.on_callback,
            F.data.startswith(CB_PREFIX),
        )

    async def start_background_tasks(self) -> None:
        if not config.TELEMOST_MAIL_ENABLED:
            return
        svc = self._service()
        if svc is None or not svc.enabled:
            self.log(
                "фоновый опрос почты не запущен (IMAP, RAG, chat_id, speaker_names)",
                level="warning",
            )
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop(), name="telemost_mail_poll")
        self.log(
            f"опрос почты Телемоста каждые {config.TELEMOST_MAIL_POLL_INTERVAL_SEC} с"
        )

    async def stop_background_tasks(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _poll_loop(self) -> None:
        interval = max(60, int(config.TELEMOST_MAIL_POLL_INTERVAL_SEC or 300))
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("telemost_mail poll: %s", e)
            await asyncio.sleep(interval)

    async def _poll_once(self) -> int:
        async with self._poll_lock:
            svc = self._service()
            if svc is None or not svc.enabled:
                return 0
            notes = await svc.poll_new_mail()
        bot = self._app.bot if self._app else None
        if not bot:
            return 0
        for note in notes:
            await self._notify_pending(bot, note)
        return len(notes)

    def _format_notify_text(self, note: dict[str, Any]) -> str:
        clf: TelemostClassification = note["classification"]
        subj = html_escape((note.get("subject") or "")[:300])
        mid = html_escape((note.get("meeting_id") or "")[:32])
        mdate = html_escape((note.get("meeting_date") or "")[:20])
        mstarted = html_escape((note.get("started_at") or "")[:48])
        when = mstarted or mdate
        meta_line = ""
        if mid or when:
            meta_line = f"№{mid}" + (f" · {when}" if when else "") + "\n"
        rec = "✅ рекомендуем в RAG" if clf.recommend_index else "⊘ скорее не клуб"
        club = "да" if clf.is_club_meeting else "нет"
        tr = "есть" if note.get("has_transcript") else "нет"
        summary = html_escape((clf.summary or "")[:800])
        prefix = "📧 <b>Телемост</b>"
        if note.get("force_reload"):
            prefix = "📧 <b>Телемост · повторное решение</b>"
        elif note.get("backfill"):
            prefix = "📧 <b>Телемост · догрузка</b>"
        return (
            f"{prefix}\n"
            f"{meta_line}"
            f"<b>{subj}</b>\n\n"
            f"{summary}\n\n"
            f"Клуб: <b>{club}</b> · {rec}\n"
            f"<i>{html_escape(clf.admin_note or '')}</i>\n"
            f"TXT: {tr}"
        )

    def _recording_kind_keyboard(self, pending_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🙏 Молитва",
                        callback_data=f"{CB_KIND}{KIND_MOLITVA}:{pending_id}",
                    ),
                    InlineKeyboardButton(
                        text="📻 Эфир",
                        callback_data=f"{CB_KIND}{KIND_EFIR}:{pending_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Другое",
                        callback_data=f"{CB_KIND}{KIND_OTHER}:{pending_id}",
                    ),
                ],
            ]
        )

    def _approval_keyboard(self, pending_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Загрузить в RAG",
                        callback_data=f"{CB_LOAD}{pending_id}",
                        style="success",
                    ),
                    InlineKeyboardButton(
                        text="Игнорировать",
                        callback_data=f"{CB_IGNORE}{pending_id}",
                        style="danger",
                    ),
                ]
            ]
        )

    async def notify_pending_note(self, bot, note: dict[str, Any]) -> None:
        await self._notify_pending(bot, note)

    async def _notify_pending(self, bot, note: dict[str, Any]) -> None:
        chat_id, topic_id = rag_admin_chat_topic()
        if not chat_id:
            return
        text = self._format_notify_text(note)
        pid = note["pending_id"]
        try:
            msg = await bot.send_message(
                chat_id,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=self._approval_keyboard(pid),
                message_thread_id=topic_id,
            )
            await self._app.user_storage.set_telemost_notify_message_id(
                uuid.UUID(pid), msg.message_id
            )
        except Exception as e:
            logger.error("telemost notify failed: %s", e)

    async def on_callback(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только для администратора.", show_alert=True)
            return

        data = (callback.data or "").strip()
        if data.startswith(CB_KIND):
            await self._on_kind_callback(callback, data)
            return
        if data.startswith(CB_LOAD):
            pid_s = data[len(CB_LOAD) :]
            await self._on_load_callback(callback, pid_s)
            return
        if data.startswith(CB_IGNORE):
            pid_s = data[len(CB_IGNORE) :]
            await self._on_ignore_callback(callback, pid_s)
            return
        await callback.answer()

    async def _on_load_callback(self, callback: CallbackQuery, pid_s: str) -> None:
        try:
            uuid.UUID(pid_s)
        except ValueError:
            await callback.answer("Некорректный id", show_alert=True)
            return

        await callback.answer()
        msg = callback.message
        if not msg:
            return
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            await callback.bot.send_message(
                msg.chat.id,
                "🎙 <b>Это запись?</b> Выберите тип — от этого зависит RAG и публикация аудио:",
                parse_mode=ParseMode.HTML,
                reply_markup=self._recording_kind_keyboard(pid_s),
                message_thread_id=msg.message_thread_id,
            )
        except Exception as e:
            logger.warning("telemost kind prompt: %s", e)

    async def _on_kind_callback(self, callback: CallbackQuery, data: str) -> None:
        rest = data[len(CB_KIND) :]
        kind, _, pid_s = rest.partition(":")
        if kind not in {KIND_EFIR, KIND_MOLITVA, KIND_OTHER}:
            await callback.answer("Неизвестный тип", show_alert=True)
            return
        try:
            pid = uuid.UUID(pid_s)
        except ValueError:
            await callback.answer("Некорректный id", show_alert=True)
            return

        svc = self._service()
        if svc is None:
            await callback.answer("RAG недоступен", show_alert=True)
            return

        await callback.answer("Индексирую…")
        n, result = await svc.index_pending(pid, recording_kind=kind)
        resolve_mail_decision(pid_s, f"load:{kind}")
        result_html = result if n > 0 else html_escape(result)

        kind_label = {
            KIND_EFIR: "Эфир",
            KIND_MOLITVA: "Молитва",
            KIND_OTHER: "Другое",
        }.get(kind, kind)

        msg = callback.message
        if msg:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            try:
                await msg.reply(
                    f"→ <b>{html_escape(kind_label)}</b>: {result_html}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning("telemost callback reply: %s", e)

    async def _on_ignore_callback(self, callback: CallbackQuery, pid_s: str) -> None:
        try:
            pid = uuid.UUID(pid_s)
        except ValueError:
            await callback.answer("Некорректный id", show_alert=True)
            return

        svc = self._service()
        if svc is None:
            await callback.answer("RAG недоступен", show_alert=True)
            return

        await svc.ignore_pending(pid)
        resolve_mail_decision(pid_s, "ignore")
        result = "Игнорировано"
        result_html = html_escape(result)

        msg = callback.message
        if msg:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            try:
                await msg.reply(
                    f"→ {result_html}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning("telemost callback reply: %s", e)

        await callback.answer(result)

    @staticmethod
    def _parse_meeting_id_arg(text: str | None) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            return ""
        digits = re.sub(r"\D", "", parts[1])
        if not digits:
            return ""
        if len(digits) == 9:
            digits = "0" + digits
        return digits

    async def cmd_telemost_load(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return
        if message.chat.type != ChatType.PRIVATE and not is_rag_admin_message(
            message.chat.id, message.message_thread_id
        ):
            admin_chat, admin_topic = rag_admin_chat_topic()
            await message.reply(
                "Команда только в админ-ветке RAG "
                f"(чат <code>{admin_chat}</code>, топик <code>{admin_topic}</code>).",
                parse_mode=ParseMode.HTML,
            )
            return

        meeting_id = self._parse_meeting_id_arg(message.text)
        if not meeting_id:
            await message.answer(
                "Формат: <code>/telemost_load 0407503331</code>\n"
                "Номер — как в письме Телемоста (№…).",
                parse_mode=ParseMode.HTML,
            )
            return

        svc = self._service()
        if svc is None or not svc.enabled:
            await message.answer("Сервис Телемост → RAG недоступен.")
            return

        note, err = await svc.force_offer_pending_by_meeting_id(meeting_id)
        if err:
            await message.answer(err, parse_mode=ParseMode.HTML)
            return
        if not note:
            await message.answer("Не удалось подготовить карточку.")
            return

        bot = self._app.bot if self._app else None
        if not bot:
            await message.answer("Бот не инициализирован.")
            return

        chat_id, topic_id = rag_admin_chat_topic()
        if not chat_id:
            await message.answer("Админ-чат RAG не настроен.")
            return

        await message.answer(
            f"♻️ Повторное решение по встрече №<code>{html_escape(meeting_id)}</code>…",
            parse_mode=ParseMode.HTML,
        )
        await self._notify_pending(bot, note)

    async def cmd_telemost_status(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return
        last_uid = await self._app.user_storage.get_telemost_mail_last_uid()
        admin_chat, admin_topic = rag_admin_chat_topic()
        lines = [
            "<b>Телемост → почта → RAG</b>",
            f"Включено: <code>{config.TELEMOST_MAIL_ENABLED}</code>",
            f"IMAP: <code>{'да' if (config.TELEMOST_MAIL_LOGIN or '').strip() else 'нет'}</code>",
            f"Админ-ветка: <code>{admin_chat}</code> / топик <code>{admin_topic}</code>",
            f"Спикер: <code>{html_escape(config.TELEMOST_MAIL_AVATAR_SPEAKER_NAMES)}</code>",
            f"Последний UID: <code>{last_uid}</code>",
        ]
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_telemost_poll(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return
        await message.answer("Опрашиваю почту…")
        n = await self._poll_once()
        await message.answer(f"Новых писем для решения: <b>{n}</b>", parse_mode=ParseMode.HTML)
