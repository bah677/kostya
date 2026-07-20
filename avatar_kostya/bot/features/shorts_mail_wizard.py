"""Рассылка шортса (video/voice) в club / biblia по reply + #club / #biblia."""

from __future__ import annotations

import logging
import secrets
import re
from datetime import datetime
from html import escape as html_escape
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.services.shorts_mail import db as mail_db
from bot.services.shorts_mail.reupload import reupload_media_via_bot
from bot.utils.rag_admin_context import is_rag_shorts_message, rag_shorts_chat_topic
from config import config

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

CB = "sm"
KW_CLUB = re.compile(r"(?i)^\s*#club\s*$")
KW_BIBLIA = re.compile(r"(?i)^\s*#biblia\s*$")
MARK_RE = re.compile(r"^[A-Za-z0-9]+$")


class ShortsMailStates(StatesGroup):
    text_mode = State()
    text_custom = State()
    club_cta = State()
    club_mark = State()
    biblia_url_mode = State()
    biblia_url_custom = State()
    btn_label = State()
    audience = State()
    first_n = State()
    custom_ids = State()
    exclude = State()
    schedule = State()
    confirm = State()


def _cb(action: str, value: str = "-") -> str:
    return f"{CB}:{action}:{value}"


def _parse_cb(data: str) -> Tuple[str, str]:
    parts = (data or "").split(":", 2)
    if len(parts) < 3 or parts[0] != CB:
        return "", ""
    return parts[1], parts[2]


class ShortsMailWizardFeature(BaseFeature):
    name = "shorts_mail_wizard"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._club_pool = None
        self._biblia_pool = None
        self._club_bot: Optional[Bot] = None
        self._biblia_bot: Optional[Bot] = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def initialize(self) -> None:
        club_dsn = getattr(config, "club_mail_database_url", "") or ""
        biblia_dsn = getattr(config, "biblia_mail_database_url", "") or ""
        if club_dsn:
            try:
                self._club_pool = await mail_db.create_pool(club_dsn)
                self.log("club mail DB pool OK")
            except Exception as e:
                logger.error("shorts_mail club pool: %s", e)
        if biblia_dsn:
            try:
                self._biblia_pool = await mail_db.create_pool(biblia_dsn)
                self.log("biblia mail DB pool OK")
            except Exception as e:
                logger.error("shorts_mail biblia pool: %s", e)

        club_tok = (getattr(config, "CLUB_BOT_TOKEN", "") or "").strip()
        biblia_tok = (getattr(config, "BIBLIA_MAIL_BOT_TOKEN", "") or "").strip()
        if club_tok:
            self._club_bot = Bot(token=club_tok)
        if biblia_tok:
            self._biblia_bot = Bot(token=biblia_tok)

    async def teardown(self) -> None:
        for pool in (self._club_pool, self._biblia_pool):
            if pool is not None:
                await pool.close()
        for b in (self._club_bot, self._biblia_bot):
            if b is not None:
                await b.session.close()

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        shorts_chat, shorts_topic = rag_shorts_chat_topic()
        keyword_filters = [
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
            F.reply_to_message,
            F.text,
            StateFilter(None),
        ]
        if shorts_chat:
            keyword_filters.insert(0, F.chat.id == shorts_chat)
        if shorts_topic:
            keyword_filters.append(F.message_thread_id == shorts_topic)

        dispatcher.message.register(
            self.on_keyword_reply,
            *keyword_filters,
        )
        dispatcher.callback_query.register(
            self.on_callback,
            F.data.startswith(f"{CB}:"),
        )
        dispatcher.message.register(
            self.on_text_custom,
            StateFilter(ShortsMailStates.text_custom),
            F.text,
        )
        dispatcher.message.register(
            self.on_club_mark,
            StateFilter(ShortsMailStates.club_mark),
            F.text,
        )
        dispatcher.message.register(
            self.on_biblia_url_custom,
            StateFilter(ShortsMailStates.biblia_url_custom),
            F.text,
        )
        dispatcher.message.register(
            self.on_btn_label,
            StateFilter(ShortsMailStates.btn_label),
            F.text,
        )
        dispatcher.message.register(
            self.on_first_n,
            StateFilter(ShortsMailStates.first_n),
            F.text,
        )
        dispatcher.message.register(
            self.on_custom_ids,
            StateFilter(ShortsMailStates.custom_ids),
            F.text,
        )
        dispatcher.message.register(
            self.on_exclude_text,
            StateFilter(ShortsMailStates.exclude),
            F.text,
        )
        dispatcher.message.register(
            self.on_schedule_text,
            StateFilter(ShortsMailStates.schedule),
            F.text,
        )

    @staticmethod
    def _extract_media(msg: Message) -> Optional[Tuple[str, str, str]]:
        """Returns (kind, file_id, default_caption_html)."""
        if msg.video:
            cap = (msg.html_text if msg.caption_entities else None) or (msg.caption or "")
            return "video", msg.video.file_id, cap.strip()
        if msg.voice:
            cap = (msg.html_text if msg.caption_entities else None) or (msg.caption or "")
            return "voice", msg.voice.file_id, cap.strip()
        if msg.audio:
            cap = (msg.html_text if msg.caption_entities else None) or (msg.caption or "")
            return "voice", msg.audio.file_id, cap.strip()
        return None

    @staticmethod
    def _message_body_html(message: Message) -> str:
        """Текст с Telegram-сущностями → HTML; иначе plain."""
        if message.entities and message.html_text:
            return (message.html_text or "").strip()
        return (message.text or "").strip()

    async def on_keyword_reply(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.reply_to_message is None:
            raise SkipHandler
        if not is_rag_shorts_message(message.chat.id, message.message_thread_id):
            raise SkipHandler
        text = (message.text or "").strip()
        if KW_CLUB.match(text):
            target = "club"
        elif KW_BIBLIA.match(text):
            target = "biblia"
        else:
            raise SkipHandler
        if not await self._is_admin(message.from_user.id):
            logger.info(
                "shorts_mail: ignore non-admin uid=%s text=%r",
                message.from_user.id,
                text,
            )
            raise SkipHandler

        media = self._extract_media(message.reply_to_message)
        if not media:
            await message.reply("Ответьте на сообщение с <b>видео</b> или <b>голосовым</b> шортсом.", parse_mode=ParseMode.HTML)
            return
        kind, file_id, caption = media

        logger.info(
            "shorts_mail: start target=%s kind=%s uid=%s reply_to=%s",
            target,
            kind,
            message.from_user.id,
            message.reply_to_message.message_id,
        )
        await state.clear()
        await state.set_state(ShortsMailStates.text_mode)
        await state.update_data(
            target=target,
            media_kind=kind,
            source_file_id=file_id,
            default_text=caption or f"Шортс ({kind})",
            reply_msg_id=message.reply_to_message.message_id,
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Оставить caption", callback_data=_cb("text", "keep"))],
                [InlineKeyboardButton(text="Свой текст", callback_data=_cb("text", "custom"))],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
            ]
        )
        preview = html_escape((caption or "(без подписи)")[:500])
        await message.reply(
            f"📬 Рассылка в <b>{html_escape(target)}</b> · медиа: <code>{html_escape(kind)}</code>\n\n"
            f"Текст сейчас:\n{preview}\n\nВыберите:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    async def on_text_custom(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        await state.update_data(mail_text=self._message_body_html(message))
        await self._after_text(message, state)

    async def on_club_mark(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        mark = (message.text or "").strip()
        if not MARK_RE.match(mark):
            await message.reply("Метка: только латиница/цифры, без «_». Пример: <code>20260716Efir</code>", parse_mode=ParseMode.HTML)
            return
        data = await state.get_data()
        preset = data.get("cta_preset")
        if preset == "promo":
            callback = f"payment_start_promo_test1week_{mark}"
            default_label = "Тестовая неделя 299₽"
        else:
            callback = f"payment_start_{mark}"
            default_label = "Вступить в клуб"
        await state.update_data(cta_callback=callback, default_btn_label=default_label)
        await state.set_state(ShortsMailStates.btn_label)
        await message.reply(
            f"Callback: <code>{callback}</code>\nТекст кнопки (или «-» для «{default_label}»):",
            parse_mode=ParseMode.HTML,
        )

    async def on_biblia_url_custom(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        url = (message.text or "").strip()
        if not url.startswith("http"):
            await message.reply("Нужен URL, начинающийся с http…")
            return
        await state.update_data(cta_url=url, default_btn_label="Открыть")
        await state.set_state(ShortsMailStates.btn_label)
        await message.reply("Текст кнопки (или «-» для «Открыть»):")

    async def on_btn_label(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        raw = (message.text or "").strip()
        data = await state.get_data()
        label = data.get("default_btn_label") or "Открыть"
        if raw and raw != "-":
            label = raw[:64]
        await state.update_data(btn_label=label)
        await self._ask_audience(message, state)

    async def on_first_n(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        try:
            n = int((message.text or "").strip())
            if n <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Введите целое N > 0")
            return
        await state.update_data(aud_segment="first_n", aud_first_n=n)
        await self._ask_exclude_or_schedule(message, state)

    async def on_custom_ids(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        ids: List[int] = []
        for chunk in re.split(r"[\s,;]+", (message.text or "").strip()):
            if not chunk:
                continue
            try:
                ids.append(int(chunk))
            except ValueError:
                await message.reply("Нужны числовые user_id через запятую/пробел")
                return
        if not ids:
            await message.reply("Список пуст")
            return
        await state.update_data(aud_segment="custom", custom_user_ids=ids)
        await self._ask_exclude_or_schedule(message, state)

    async def on_exclude_text(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        raw = (message.text or "").strip()
        exclude_ids: List[int] = []
        if raw and raw != "-":
            for chunk in raw.replace(" ", "").split(","):
                if not chunk:
                    continue
                try:
                    exclude_ids.append(int(chunk))
                except ValueError:
                    await message.reply("Id кампаний через запятую, либо «-»")
                    return
        exclude_ids = sorted(set(exclude_ids))
        data = await state.get_data()
        existing = set(int(x) for x in (data.get("exclude_user_ids") or []))
        if self._biblia_pool and exclude_ids:
            campaign_uids = await mail_db.audience_uids_for_campaigns(
                self._biblia_pool, exclude_ids
            )
            existing.update(campaign_uids)
        await state.update_data(
            exclude_campaign_ids=exclude_ids,
            exclude_user_ids=sorted(existing),
        )
        await self._ask_schedule(message, state)

    async def on_schedule_text(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await self._is_admin(message.from_user.id):
            return
        raw = (message.text or "").strip()
        when = self._parse_schedule(raw)
        if when is None:
            await message.reply("Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (МСК)", parse_mode=ParseMode.HTML)
            return
        await state.update_data(scheduled_at=when.isoformat())
        await self._show_confirm(message, state)

    def _parse_schedule(self, raw: str) -> Optional[datetime]:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.replace(tzinfo=MSK).replace(tzinfo=None)
            except ValueError:
                continue
        return None

    async def _after_text(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        if data.get("target") == "club":
            await state.set_state(ShortsMailStates.club_cta)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Промо-неделя 299₽ + метка", callback_data=_cb("cta", "promo"))],
                    [InlineKeyboardButton(text="payment_start + метка", callback_data=_cb("cta", "start"))],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
                ]
            )
            await message.answer(
                "Callback для клуба:\n"
                "• <code>payment_start_promo_test1week_&lt;метка&gt;</code>\n"
                "• <code>payment_start_&lt;метка&gt;</code>\n\n"
                "Метка без «_» внутри (пример: <code>20260716Efir</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await state.set_state(ShortsMailStates.biblia_url_mode)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Сгенерировать ref URL", callback_data=_cb("url", "gen"))],
                    [InlineKeyboardButton(text="Ввести URL вручную", callback_data=_cb("url", "manual"))],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
                ]
            )
            await message.answer("URL кнопки для Библии:", reply_markup=kb)

    async def _ask_audience(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        await state.set_state(ShortsMailStates.audience)
        if data.get("target") == "club":
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="1️⃣ Все активные", callback_data=_cb("aud", "all"))],
                    [InlineKeyboardButton(text="2️⃣ С лицензией", callback_data=_cb("aud", "has_license"))],
                    [InlineKeyboardButton(text="3️⃣ Без лицензии", callback_data=_cb("aud", "no_license"))],
                    [InlineKeyboardButton(text="✏️ Свой список id", callback_data=_cb("aud", "custom"))],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
                ]
            )
        else:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="1️⃣ Все", callback_data=_cb("aud", "all"))],
                    [InlineKeyboardButton(text="2️⃣ Первые N", callback_data=_cb("aud", "first_n"))],
                    [InlineKeyboardButton(text="3️⃣ Своя аудитория (id)", callback_data=_cb("aud", "custom"))],
                    [InlineKeyboardButton(text="4️⃣ Кто делал донат", callback_data=_cb("aud", "donors"))],
                    [InlineKeyboardButton(text="5️⃣ Кто делал 2+ доната", callback_data=_cb("aud", "donors_2plus"))],
                    [InlineKeyboardButton(text="6️⃣ В челлендже", callback_data=_cb("aud", "challenge_in"))],
                    [InlineKeyboardButton(text="7️⃣ Не в челлендже", callback_data=_cb("aud", "challenge_not_in"))],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
                ]
            )
        await message.answer("Аудитория (как в мастере рассылок):", reply_markup=kb)

    async def _ask_exclude_or_schedule(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        if data.get("target") == "biblia":
            await self._ask_exclude(message, state)
        else:
            await self._ask_schedule(message, state)

    async def _ask_exclude(self, message: Message, state: FSMContext) -> None:
        await state.set_state(ShortsMailStates.exclude)
        lines = ["Исключения (как в /new_mailing Библии):"]
        if self._biblia_pool:
            recent = await mail_db.list_recent_campaigns(self._biblia_pool, limit=15)
            if recent:
                lines.append("Недавние кампании:")
                for r in recent:
                    lines.append(f"• <code>{r['id']}</code> — {r.get('name')}")
        lines.append(
            "\nВведите id кампаний через запятую, кого исключить из аудитории.\n"
            "Пустая строка / <code>-</code> — не исключать по кампаниям."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚫 Исключить в челлендже", callback_data=_cb("ex", "challenge"))],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
            ]
        )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _ask_schedule(self, message: Message, state: FSMContext) -> None:
        await state.set_state(ShortsMailStates.schedule)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚡ Сейчас", callback_data=_cb("sched", "now"))],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
            ]
        )
        await message.answer(
            "Когда отправить?\n⚡ Сейчас или дата <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (МСК)",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    async def _resolve_uids(self, data: Dict[str, Any]) -> List[int]:
        target = data.get("target")
        seg = str(data.get("aud_segment") or "")
        exclude = set(int(x) for x in (data.get("exclude_user_ids") or []))

        if target == "club":
            if not self._club_pool:
                raise RuntimeError("club DB pool не готов")
            if seg == "custom":
                base = [int(x) for x in (data.get("custom_user_ids") or [])]
            else:
                base = await mail_db.fetch_club_audience(self._club_pool, seg)
            return [u for u in base if u not in exclude]

        if not self._biblia_pool:
            raise RuntimeError("biblia DB pool не готов")
        challenge = set(await mail_db.fetch_biblia_challenge_uids(self._biblia_pool))
        if seg == "custom":
            base = [int(x) for x in (data.get("custom_user_ids") or [])]
        elif seg in ("all", "first_n"):
            base = await mail_db.fetch_biblia_active(self._biblia_pool)
        elif seg == "donors":
            base = await mail_db.fetch_biblia_donors(self._biblia_pool, min_donations=1)
        elif seg == "donors_2plus":
            base = await mail_db.fetch_biblia_donors(self._biblia_pool, min_donations=2)
        elif seg == "challenge_in":
            base = sorted(challenge)
        elif seg == "challenge_not_in":
            all_a = await mail_db.fetch_biblia_active(self._biblia_pool)
            base = [u for u in all_a if u not in challenge]
        else:
            raise ValueError(seg)
        filtered = [u for u in base if u not in exclude]
        if seg == "first_n":
            n = int(data.get("aud_first_n") or 0)
            if n > 0:
                return filtered[:n]
        return filtered

    async def _show_confirm(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        try:
            uids = await self._resolve_uids(data)
        except Exception as e:
            await message.answer(f"Ошибка аудитории: {e}")
            return
        await state.update_data(resolved_uids=uids)
        await state.set_state(ShortsMailStates.confirm)
        when = html_escape(str(data.get("scheduled_at") or "?"))
        cta = html_escape(str(data.get("cta_callback") or data.get("cta_url") or "?"))
        mail_preview = html_escape((data.get("mail_text") or "")[:400])
        btn = html_escape(str(data.get("btn_label") or ""))
        seg = html_escape(str(data.get("aud_segment") or ""))
        kind = html_escape(str(data.get("media_kind") or ""))
        target = html_escape(str(data.get("target") or ""))
        blob = (
            f"<b>Подтверждение</b>\n"
            f"Цель: <b>{target}</b>\n"
            f"Медиа: <code>{kind}</code>\n"
            f"Текст:\n{mail_preview}\n"
            f"Кнопка: {btn} → <code>{cta}</code>\n"
            f"Сегмент: {seg}\n"
            f"Получателей: <b>{len(uids)}</b>\n"
            f"Время: <code>{when}</code>\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Создать кампанию", callback_data=_cb("ok", "yes"))],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("x", "cancel"))],
            ]
        )
        try:
            await message.answer(blob, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            logger.warning("shorts_mail confirm HTML failed: %s — plain fallback", e)
            await message.answer(
                "Подтверждение (plain):\n"
                f"Цель: {data.get('target')}\n"
                f"Медиа: {data.get('media_kind')}\n"
                f"Текст: {(data.get('mail_text') or '')[:400]}\n"
                f"Кнопка: {data.get('btn_label')} → {data.get('cta_callback') or data.get('cta_url')}\n"
                f"Сегмент: {data.get('aud_segment')}\n"
                f"Получателей: {len(uids)}\n"
                f"Время: {data.get('scheduled_at')}\n",
                reply_markup=kb,
            )

    async def on_callback(self, query: CallbackQuery, state: FSMContext) -> None:
        if query.from_user is None or query.message is None:
            await query.answer()
            return
        if not await self._is_admin(query.from_user.id):
            await query.answer("⛔", show_alert=True)
            return
        action, value = _parse_cb(query.data or "")
        if action == "x":
            await state.clear()
            await query.message.edit_text("❌ Отменено.")
            await query.answer()
            return

        st = await state.get_state()

        if action == "text" and st == ShortsMailStates.text_mode.state:
            data = await state.get_data()
            if value == "keep":
                await state.update_data(mail_text=data.get("default_text") or "")
                await query.answer()
                await self._after_text(query.message, state)
                return
            if value == "custom":
                await state.set_state(ShortsMailStates.text_custom)
                await query.message.edit_text("Пришлите текст рассылки:")
                await query.answer()
                return

        if action == "cta" and st == ShortsMailStates.club_cta.state:
            await state.update_data(cta_preset=value)
            await state.set_state(ShortsMailStates.club_mark)
            await query.message.edit_text(
                "Введите метку (латиница/цифры, без «_»):"
            )
            await query.answer()
            return

        if action == "url" and st == ShortsMailStates.biblia_url_mode.state:
            if value == "gen":
                token = secrets.token_hex(4)
                un = (getattr(config, "BIBLIA_BOT_USERNAME", "") or "otvet_iz_biblii_bot").lstrip("@")
                url = f"https://t.me/{un}?start=short_{token}"
                await state.update_data(
                    cta_url=url,
                    touch_token=f"short_{token}",
                    default_btn_label="Открыть",
                )
                await state.set_state(ShortsMailStates.btn_label)
                await query.message.edit_text(
                    f"URL: <code>{url}</code>\nТекст кнопки (или «-»):",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await state.set_state(ShortsMailStates.biblia_url_custom)
                await query.message.edit_text("Пришлите URL:")
            await query.answer()
            return

        if action == "aud" and st == ShortsMailStates.audience.state:
            if value == "custom":
                await state.set_state(ShortsMailStates.custom_ids)
                await query.message.edit_text("Пришлите user_id через запятую/пробел:")
            elif value == "first_n":
                await state.set_state(ShortsMailStates.first_n)
                await query.message.edit_text("Сколько первых N?")
            else:
                await state.update_data(aud_segment=value)
                await query.message.edit_text("⏳ …")
                await self._ask_exclude_or_schedule(query.message, state)
            await query.answer()
            return

        if action == "ex" and st == ShortsMailStates.exclude.state and value == "challenge":
            uids = []
            if self._biblia_pool:
                uids = await mail_db.fetch_biblia_challenge_uids(self._biblia_pool)
            data = await state.get_data()
            existing = set(int(x) for x in (data.get("exclude_user_ids") or []))
            existing.update(uids)
            await state.update_data(
                exclude_challenge_users=True,
                exclude_user_ids=sorted(existing),
            )
            await query.answer(f"Исключено {len(uids)} в челлендже")
            await query.message.answer(
                f"✅ +{len(uids)} из челленджа в исключения.\n"
                "Введите id кампаний или <code>-</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        if action == "sched" and st == ShortsMailStates.schedule.state and value == "now":
            await state.update_data(scheduled_at=datetime.now().isoformat(timespec="seconds"))
            await query.answer()
            await self._show_confirm(query.message, state)
            return

        if action == "ok" and st == ShortsMailStates.confirm.state and value == "yes":
            await query.answer()
            await query.message.edit_text("⏳ Создаю кампанию (перезаливка медиа)…")
            await self._finalize(query.message, state, query.from_user.id)
            return

        await query.answer()

    async def _finalize(self, message: Message, state: FSMContext, who: int) -> None:
        data = await state.get_data()
        target = data.get("target")
        kind = data.get("media_kind")
        source_file_id = data.get("source_file_id")
        uids = list(data.get("resolved_uids") or [])
        if not uids:
            try:
                uids = await self._resolve_uids(data)
            except Exception as e:
                await message.answer(f"❌ Аудитория: {e}")
                await state.clear()
                return

        source_bot = self._app.bot if self._app else None
        target_bot = self._club_bot if target == "club" else self._biblia_bot
        pool = self._club_pool if target == "club" else self._biblia_pool
        if not source_bot or not target_bot or not pool:
            await message.answer("❌ Нет бота/пула БД для цели. Проверьте CLUB_* / BIBLIA_MAIL_* в .env")
            await state.clear()
            return

        stash = int(config.SUPER_ADMIN_ID or 0)
        try:
            new_file_id, media_type = await reupload_media_via_bot(
                source_bot=source_bot,
                target_bot=target_bot,
                file_id=source_file_id,
                kind=kind,
                stash_chat_id=stash,
                filename_hint=f"short_{target}",
            )
        except Exception as e:
            logger.exception("reupload failed")
            await message.answer(f"❌ Перезаливка медиа не удалась: {e}")
            await state.clear()
            return

        if target == "club":
            buttons = [
                {
                    "text": data.get("btn_label") or "Вступить",
                    "style": "success",
                    "callback": data.get("cta_callback"),
                }
            ]
        else:
            buttons = [
                {
                    "text": data.get("btn_label") or "Открыть",
                    "style": "success",
                    "url": data.get("cta_url"),
                }
            ]

        raw_when = data.get("scheduled_at")
        try:
            when = datetime.fromisoformat(str(raw_when))
        except Exception:
            when = datetime.now()

        name = f"shorts:{target}:{kind}:{when.strftime('%Y%m%d_%H%M')}"
        touch = data.get("touch_token")
        if touch:
            name = f"{name}:{touch}"

        attachments = [{"type": media_type, "file_id": new_file_id}]
        try:
            cid = await mail_db.create_mailing_campaign(
                pool,
                name=name[:250],
                text=str(data.get("mail_text") or ""),
                scheduled_at=when,
                created_by=who,
                buttons=buttons,
                attachments=attachments,
            )
            if not cid:
                raise RuntimeError("INSERT mailing_campaigns вернул NULL")
            added = await mail_db.add_audience(pool, cid, uids)
        except Exception as e:
            logger.exception("create campaign failed")
            await message.answer(f"❌ Не удалось создать кампанию: {e}")
            await state.clear()
            return

        await state.clear()
        await message.answer(
            f"✅ Кампания <code>{cid}</code> в <b>{target}</b>\n"
            f"Аудитория: добавлено <b>{added}</b> / запрошено {len(uids)}\n"
            f"Время: <code>{when}</code>\n"
            f"Медиа перезалито ({media_type}).",
            parse_mode=ParseMode.HTML,
        )
