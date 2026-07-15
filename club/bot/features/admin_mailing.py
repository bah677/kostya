"""Админ-рассылки: FSM `/new_mailing` в личке с ботом (только для admins)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_telegram_admin
from bot.texts import ru_admin_mailing as aml_txt
from storage.mailing_storage import MailingStorage
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


class AMLCallback(CallbackData, prefix="aml"):
    """Короткий префикс под лимит callback_data Telegram."""

    a: str
    v: str


class AdminMailingStates(StatesGroup):
    name = State()
    text_body = State()
    parse_sel = State()
    schedule_txt = State()
    ref_link = State()
    has_media = State()
    media_batch = State()
    has_buttons = State()
    button_text_in = State()
    button_kind = State()
    button_style_sel = State()
    button_value_in = State()
    button_more = State()
    audience = State()
    custom_ids = State()
    confirm = State()


_MEDIA_HELP = (
    "📎 <b>Пришлите медиафайлы по одному</b> (фото / видео / документ / голос / кружок)."
    "\nПодпись к сообщению не идёт в рассылку — только текст кампании, заданный ранее."
    "\nКомандой <code>/done</code> или кнопкой «Готово» ниже закончите пакет."
)


def _yes_no_kb(action: str) -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=cb(a=action, v="y").pack()),
                InlineKeyboardButton(text="❌ Нет", callback_data=cb(a=action, v="n").pack()),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _parse_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 HTML", callback_data=cb(a="parse", v="HTML").pack()),
                InlineKeyboardButton(
                    text="📄 Markdown", callback_data=cb(a="parse", v="MarkdownV2").pack()
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _aud_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1️⃣ Все активные",
                    callback_data=cb(a="aud", v="all").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="2️⃣ С лицензией",
                    callback_data=cb(a="aud", v="has_license").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="3️⃣ Без действующей лицензии",
                    callback_data=cb(a="aud", v="no_license").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Свой список id",
                    callback_data=cb(a="aud", v="custom").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _media_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=aml_txt.BTN_MEDIA_DONE,
                    callback_data=cb(a="med", v="done").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать кампанию",
                    callback_data=cb(a="ok", v="yes").pack(),
                ),
                InlineKeyboardButton(text="❌ Нет", callback_data=cb(a="ok", v="no").pack()),
            ],
        ]
    )


def _schedule_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Сейчас",
                    callback_data=cb(a="sched", v="now").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _btn_kind_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=aml_txt.BTN_KIND_CALLBACK,
                    callback_data=cb(a="bk", v="callback").pack(),
                ),
                InlineKeyboardButton(
                    text=aml_txt.BTN_KIND_URL,
                    callback_data=cb(a="bk", v="url").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=aml_txt.BTN_CANCEL, callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _btn_style_kb() -> InlineKeyboardMarkup:
    cb = AMLCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=aml_txt.BTN_STYLE_SUCCESS,
                    callback_data=cb(a="bs", v="success").pack(),
                ),
                InlineKeyboardButton(
                    text=aml_txt.BTN_STYLE_PRIMARY,
                    callback_data=cb(a="bs", v="primary").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=aml_txt.BTN_STYLE_DANGER,
                    callback_data=cb(a="bs", v="danger").pack(),
                ),
                InlineKeyboardButton(
                    text=aml_txt.BTN_STYLE_DEFAULT,
                    callback_data=cb(a="bs", v="none").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=aml_txt.BTN_CANCEL, callback_data=cb(a="x", v="cancel").pack()
                )
            ],
        ]
    )


def _attachment_parts_from_message(m: Message) -> Optional[List[Dict[str, str]]]:
    parts: List[Dict[str, str]] = []
    if m.photo:
        parts.append({"type": "photo", "file_id": m.photo[-1].file_id})
    elif m.video:
        parts.append({"type": "video", "file_id": m.video.file_id})
    elif m.animation:
        parts.append({"type": "animation", "file_id": m.animation.file_id})
    elif m.document:
        parts.append({"type": "document", "file_id": m.document.file_id})
    elif m.voice:
        parts.append({"type": "voice", "file_id": m.voice.file_id})
    elif m.video_note:
        parts.append({"type": "video_note", "file_id": m.video_note.file_id})
    elif m.audio:
        parts.append({"type": "voice", "file_id": m.audio.file_id})
    return parts or None


def register_admin_mailing_handlers(
    dp: Dispatcher, user_storage: UserStorage, bot: Bot
) -> None:
    ms_singleton: Dict[str, MailingStorage] = {}

    async def ms() -> MailingStorage:
        if "x" not in ms_singleton:
            ms_singleton["x"] = MailingStorage(user_storage)
        return ms_singleton["x"]

    admin_chat_filters = (F.chat.type == ChatType.PRIVATE,)

    async def uid_ok(uid: Optional[int]) -> bool:
        if uid is None:
            return False
        return await is_telegram_admin(user_storage, uid)

    async def _cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("❌ Создание рассылки отменено.")

    dp.message.register(_cancel, *admin_chat_filters, Command("cancel"))

    async def cmd_new(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            await message.reply(
                "⛔ Нет доступа. Telegram ID должен быть в таблице <code>admins</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.clear()
        await state.update_data(
            created_by=message.from_user.id, attachments=[], buttons=None
        )
        await state.set_state(AdminMailingStates.name)
        await message.answer(
            "📧 <b>Новая рассылка (club)</b>\n\nВведите <b>внутреннее имя</b> кампании:",
            parse_mode=ParseMode.HTML,
        )

    dp.message.register(cmd_new, *admin_chat_filters, Command("new_mailing"))

    async def recv_name(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        name = (message.text or "").strip()
        if not name or len(name) > 255:
            await message.reply("❌ Название пустое или длиннее 255 символов.")
            return
        await state.update_data(name=name)
        await state.set_state(AdminMailingStates.text_body)
        await message.answer(
            "📝 Введите <b>текст сообщения</b> для рассылки:",
            parse_mode=ParseMode.HTML,
        )

    dp.message.register(recv_name, *admin_chat_filters, AdminMailingStates.name, F.text)

    async def recv_text_body(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        body = (message.text or "").strip()
        if not body:
            await message.reply("❌ Текст не может быть пустым.")
            return
        await state.update_data(text=body)
        await state.set_state(AdminMailingStates.parse_sel)
        await message.answer(
            "📄 Выберите <b>режим форматирования</b>:",
            parse_mode=ParseMode.HTML,
            reply_markup=_parse_kb(),
        )

    dp.message.register(recv_text_body, *admin_chat_filters, AdminMailingStates.text_body, F.text)

    async def recv_schedule_txt(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        raw = (message.text or "").strip()
        try:
            when = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            ex = aml_txt.schedule_example_dt()
            await message.reply(
                f"{aml_txt.ERR_SCHEDULE_FORMAT}\nПример: <code>{ex}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(scheduled_at=when)
        await state.set_state(AdminMailingStates.ref_link)
        await message.answer(
            "🔗 Добавлять к тексту персональный <code>/start ref_&lt;id&gt;</code>?",
            parse_mode=ParseMode.HTML,
            reply_markup=_yes_no_kb("ref"),
        )

    dp.message.register(
        recv_schedule_txt, *admin_chat_filters, AdminMailingStates.schedule_txt, F.text
    )

    media_types = (
        F.photo
        | F.video
        | F.document
        | F.voice
        | F.video_note
        | F.animation
        | F.audio
    )

    async def recv_med_item(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        parts = _attachment_parts_from_message(message)
        if not parts:
            await message.reply(
                "❌ Нужно фото, видео, документ, голос / аудио или видеокружок."
            )
            return
        data = await state.get_data()
        att = list(data.get("attachments") or [])
        att.extend(parts)
        await state.update_data(attachments=att)
        await message.reply(
            aml_txt.media_added_html(added=len(parts), total=len(att)),
            parse_mode=ParseMode.HTML,
            reply_markup=_media_kb(),
        )

    dp.message.register(
        recv_med_item,
        *admin_chat_filters,
        AdminMailingStates.media_batch,
        media_types,
    )

    async def recv_med_done_cmd(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        await state.set_state(AdminMailingStates.has_buttons)
        data = await state.get_data()
        n = len(data.get("attachments") or [])
        await message.answer(
            aml_txt.media_ready_prompt_html(count=n),
            parse_mode=ParseMode.HTML,
            reply_markup=_yes_no_kb("hb"),
        )

    dp.message.register(
        recv_med_done_cmd,
        *admin_chat_filters,
        AdminMailingStates.media_batch,
        Command("done"),
    )

    async def recv_btn_txt(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        t = (message.text or "").strip()
        if not t:
            await message.reply("❌ Текст пуст.")
            return
        await state.update_data(btn_text=t)
        await state.set_state(AdminMailingStates.button_kind)
        await message.answer("Тип кнопки:", reply_markup=_btn_kind_kb())

    dp.message.register(
        recv_btn_txt, *admin_chat_filters, AdminMailingStates.button_text_in, F.text
    )

    async def recv_btn_val(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        val = (message.text or "").strip()
        if not val:
            await message.reply("❌ Пусто.")
            return
        data = await state.get_data()
        kind = data.get("btn_kind")
        btn_title = data.get("btn_text")
        btn: Dict[str, Any] = {"text": btn_title}
        if kind == "callback":
            btn["callback"] = val
        else:
            btn["url"] = val
        style = data.get("btn_style")
        if style and style != "none":
            btn["style"] = style
        buttons = list(data.get("buttons") or [])
        buttons.append(btn)
        await state.update_data(buttons=buttons)
        await state.set_state(AdminMailingStates.button_more)
        await message.answer(
            aml_txt.button_added_html(total=len(buttons)),
            parse_mode=ParseMode.HTML,
            reply_markup=_yes_no_kb("bm"),
        )

    dp.message.register(
        recv_btn_val, *admin_chat_filters, AdminMailingStates.button_value_in, F.text
    )

    async def recv_custom_ids(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        raw = (message.text or "").replace(" ", "")
        uids: List[int] = []
        for chunk in raw.split(","):
            if not chunk:
                continue
            try:
                uids.append(int(chunk))
            except ValueError:
                await message.reply("❌ Нужны целые Telegram id через запятую.")
                return
        uniq = sorted(set(uids))
        if not uniq:
            await message.reply("❌ Пусто.")
            return
        await state.update_data(custom_user_ids=uniq, aud_segment="custom")
        await _show_confirm_message(message.chat.id, state, bot)

    dp.message.register(
        recv_custom_ids, *admin_chat_filters, AdminMailingStates.custom_ids, F.text
    )

    def _audience_sql(segment: str) -> str:
        if segment == "all":
            return "SELECT user_id FROM users WHERE is_active = TRUE"
        if segment == "has_license":
            return """
                SELECT u.user_id FROM users u WHERE u.is_active = TRUE
                AND EXISTS (
                  SELECT 1 FROM license l
                  WHERE l.user_id = u.user_id AND l.expires_at > NOW()
                )
            """
        if segment == "no_license":
            return """
                SELECT u.user_id FROM users u WHERE u.is_active = TRUE
                AND NOT EXISTS (
                  SELECT 1 FROM license l
                  WHERE l.user_id = u.user_id AND l.expires_at > NOW()
                )
            """
        raise ValueError(segment)

    async def _gather_user_ids(state: FSMContext) -> List[int]:
        data = await state.get_data()
        if data.get("aud_segment") == "custom":
            return list(data.get("custom_user_ids") or [])
        seg = str(data.get("aud_segment"))
        q = _audience_sql(seg)
        async with user_storage.get_connection() as conn:
            rows = await conn.fetch(q)
        return [int(r["user_id"]) for r in rows]

    async def _confirm_blob(state: FSMContext) -> str:
        data = await state.get_data()
        seg = data.get("aud_segment", "?")
        if seg == "custom":
            nh = len(data.get("custom_user_ids") or [])
        else:
            try:
                nh = len(await _gather_user_ids(state))
            except Exception:
                nh = "?"
        att = data.get("attachments")
        if att is not None and len(att) == 0:
            att = None
        return aml_txt.confirm_blob_html(
            name=data.get("name"),
            text=str(data.get("text") or ""),
            when=data.get("scheduled_at"),
            parse_mode=str(data.get("parse_mode", "HTML")),
            has_ref_link=bool(data.get("has_ref_link")),
            attachments=att,
            buttons=data.get("buttons"),
            segment=seg,
            recipient_hint=nh,
            custom_user_ids=data.get("custom_user_ids"),
        )

    async def _show_confirm_message(chat_id: int, state: FSMContext, b: Bot) -> None:
        await state.set_state(AdminMailingStates.confirm)
        await b.send_message(
            chat_id,
            await _confirm_blob(state),
            parse_mode=ParseMode.HTML,
            reply_markup=_confirm_kb(),
        )

    async def _finalize_campaign(chat_id: int, state: FSMContext, who: int, b: Bot) -> None:
        data = await state.get_data()
        uids = await _gather_user_ids(state)
        if not uids:
            await b.send_message(chat_id, "📭 Нет получателей в сегменте.")
            await state.clear()
            return

        attachments: Optional[List[Dict[str, str]]] = data.get("attachments")
        if attachments is not None and len(attachments) == 0:
            attachments = None

        mt = fid = None
        if attachments and len(attachments) == 1:
            mt, fid = attachments[0]["type"], attachments[0]["file_id"]

        campaign_row: Dict[str, Any] = {
            "name": data["name"],
            "text": data["text"],
            "parse_mode": data.get("parse_mode", "HTML"),
            "scheduled_at": data["scheduled_at"],
            "has_ref_link": bool(data.get("has_ref_link")),
            "buttons": data.get("buttons") or [],
            "created_by": who,
            "media_type": mt,
            "media_file_id": fid,
            "attachments": attachments,
        }

        m = await ms()
        cid_new = await m.create_campaign(campaign_row)
        if not cid_new:
            await b.send_message(chat_id, "❌ Не удалось сохранить кампанию (см. лог БД).")
            await state.clear()
            return
        added = await m.add_audience_batch(cid_new, uids)
        await state.clear()
        await b.send_message(
            chat_id,
            f"✅ Кампания <code>{cid_new}</code>, аудитория: добавлено <b>{added}</b> строк "
            f"из <b>{len(uids)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        logger.info("Admin mailing saved campaign_id=%s users=%s", cid_new, len(uids))

    async def aml_callback(
        query: CallbackQuery, state: FSMContext, callback_data: AMLCallback
    ) -> None:
        if query.message is None or query.from_user is None:
            await query.answer()
            return
        if query.message.chat.type != ChatType.PRIVATE or not await uid_ok(query.from_user.id):
            await query.answer("⛔", show_alert=True)
            return

        chat_id = query.message.chat.id
        a = callback_data.a
        v = callback_data.v
        mm = getattr(query.message, "message_id", 0)

        if a == "x" and v == "cancel":
            await state.clear()
            await query.message.edit_text("❌ Отменено.")
            await query.answer()
            return

        st = await state.get_state()

        if a == "parse" and st == AdminMailingStates.parse_sel.state:
            await state.update_data(parse_mode=v if v != "MarkdownV2" else "MarkdownV2")
            await state.set_state(AdminMailingStates.schedule_txt)
            await query.message.edit_text(
                aml_txt.prompt_schedule_html(),
                parse_mode=ParseMode.HTML,
                reply_markup=_schedule_kb(),
            )
            await query.answer()
            return

        if a == "sched" and st == AdminMailingStates.schedule_txt.state and v == "now":
            await state.update_data(scheduled_at=datetime.now())
            await state.set_state(AdminMailingStates.ref_link)
            await query.message.edit_text(
                "🔗 Рефлинк с id каждому получателю?",
                parse_mode=ParseMode.HTML,
                reply_markup=_yes_no_kb("ref"),
            )
            await query.answer()
            return

        if a == "ref" and st == AdminMailingStates.ref_link.state:
            await state.update_data(has_ref_link=(v == "y"))
            await state.set_state(AdminMailingStates.has_media)
            await query.message.edit_text(
                "📎 Прикладываем медиа?", parse_mode=ParseMode.HTML, reply_markup=_yes_no_kb("hm")
            )
            await query.answer()
            return

        if a == "hm" and st == AdminMailingStates.has_media.state:
            if v == "y":
                await state.update_data(attachments=[])
                await state.set_state(AdminMailingStates.media_batch)
                await query.message.edit_text(_MEDIA_HELP, parse_mode=ParseMode.HTML)
                await bot.send_message(
                    chat_id,
                    "Кнопку «Готово» можете нажать тут:",
                    reply_to_message_id=int(mm),
                    reply_markup=_media_kb(),
                )
            else:
                await state.update_data(attachments=None)
                await state.set_state(AdminMailingStates.has_buttons)
                await query.message.edit_text(
                    aml_txt.PROMPT_INLINE_BUTTON_HTML,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_yes_no_kb("hb"),
                )
            await query.answer()
            return

        if a == "med" and st == AdminMailingStates.media_batch.state and v == "done":
            await state.set_state(AdminMailingStates.has_buttons)
            d_med = await state.get_data()
            n_med = len(d_med.get("attachments") or [])
            await query.message.edit_text(
                aml_txt.media_ready_short_html(count=n_med),
                parse_mode=ParseMode.HTML,
            )
            await bot.send_message(
                chat_id,
                reply_to_message_id=int(mm),
                text="Выбор:",
                reply_markup=_yes_no_kb("hb"),
            )
            await query.answer()
            return

        if a == "hb" and st == AdminMailingStates.has_buttons.state:
            if v == "y":
                await state.update_data(buttons=[])
                await state.set_state(AdminMailingStates.button_text_in)
                await query.message.edit_text(aml_txt.PROMPT_BTN_TEXT_USERS)
            else:
                await state.update_data(buttons=None)
                await state.set_state(AdminMailingStates.audience)
                await query.message.edit_text(
                    aml_txt.PROMPT_SEGMENT_HTML,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_aud_kb(),
                )
            await query.answer()
            return

        if a == "bm" and st == AdminMailingStates.button_more.state:
            if v == "y":
                d_bm = await state.get_data()
                n = len(d_bm.get("buttons") or []) + 1
                await state.set_state(AdminMailingStates.button_text_in)
                await query.message.edit_text(aml_txt.prompt_btn_text_nth_html(n=n))
            else:
                await state.set_state(AdminMailingStates.audience)
                await query.message.edit_text(
                    aml_txt.PROMPT_AUDIENCE_HTML,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_aud_kb(),
                )
            await query.answer()
            return

        if a == "bk" and st == AdminMailingStates.button_kind.state:
            await state.update_data(btn_kind=v)
            await state.set_state(AdminMailingStates.button_style_sel)
            await query.message.edit_text(
                aml_txt.PROMPT_BUTTON_STYLE_HTML,
                parse_mode=ParseMode.HTML,
                reply_markup=_btn_style_kb(),
            )
            await query.answer()
            return

        if a == "bs" and st == AdminMailingStates.button_style_sel.state:
            await state.update_data(btn_style=v)
            await state.set_state(AdminMailingStates.button_value_in)
            d_bs = await state.get_data()
            kind = d_bs.get("btn_kind")
            ht = (
                aml_txt.prompt_callback_data_html()
                if kind == "callback"
                else aml_txt.PROMPT_HTTPS_URL_HTML
            )
            await query.message.edit_text(ht, parse_mode=ParseMode.HTML)
            await query.answer()
            return

        if a == "aud" and st == AdminMailingStates.audience.state:
            if v == "custom":
                await state.set_state(AdminMailingStates.custom_ids)
                await query.message.edit_text(
                    aml_txt.prompt_custom_user_ids_html(),
                    parse_mode=ParseMode.HTML,
                )
            else:
                await state.update_data(aud_segment=v)
                await _show_confirm_message(chat_id, state, bot)
            await query.answer()
            return

        if a == "ok" and st == AdminMailingStates.confirm.state:
            await query.answer()
            if v != "yes":
                await query.message.edit_text("❌ Не сохранено.")
                await state.clear()
                return
            await query.message.edit_text("⏳ Создаём кампанию…")
            await _finalize_campaign(chat_id, state, query.from_user.id, bot)
            return

        await query.answer()

    dp.callback_query.register(aml_callback, AMLCallback.filter())
