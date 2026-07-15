"""Команда /more: как в legacy — кнопки из bot_content + callback ``more_button_<id>``."""

import logging
from typing import TYPE_CHECKING, List, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.features.base import BaseFeature
from bot.utils.telegram_html_async import normalize_llm_reply_for_telegram_async

if TYPE_CHECKING:
    from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

# Fallback, если таблица bot_content пуста / миграция не применена
_PRESET_FAQ_ITEMS: tuple[tuple[str, str], ...] = (
    ("💬 Тревога, страх", "Мне сейчас очень тревожно и страшно. Подскажи, что может говорить об этом Новый Завет."),
    ("🙏 Усталость, выгорание", "Я очень устал(а) душой и телом. Какие места Писания могут поддержать?"),
    ("❤️ Отношения", "У меня сложности в отношениях. Помоги увидеть библейский взгляд на это мягко и по делу."),
    ("✨ Смысл, надежда", "Ищу надежду и смысл. Какие стихи Нового Завета об этом особенно уместны?"),
)

_MORE_INTRO_LEGACY = (
    "Это не просто кнопки.\n"
    "Это чувства, которые сложно сформулировать.\n"
    "Если узнаешь своё — нажми.\n"
    "Я расскажу, что говорит об этом состоянии Священное Писание."
)


async def _clear_support_if_active(state: FSMContext) -> None:
    """Как в legacy _handle_more_button: прервать сценарий поддержки."""
    cur = await state.get_state()
    if cur and "SupportStates" in str(cur):
        await state.clear()


def _build_more_keyboard_db(buttons: List[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for btn in buttons:
        bid = btn.get("id")
        if bid is None:
            continue
        builder.add(
            InlineKeyboardButton(
                text=str(btn.get("button_text") or "…"),
                callback_data=f"more_button_{int(bid)}",
            )
        )
    builder.adjust(2)
    return builder.as_markup()


class FrequentQuestionsFeature(BaseFeature):
    name = "frequent_questions"

    def __init__(
        self,
        feature_manager,
        user_storage: Optional["UserStorage"] = None,
    ):
        super().__init__()
        self.feature_manager = feature_manager
        self.user_storage = user_storage
        self.bot = None

    def set_bot(self, app) -> None:
        self.bot = app.bot if app is not None else None

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.on_more_command, Command(commands=["more"]))
        dp.callback_query.register(self.on_preset_faq, F.data.startswith("preset_faq_"))
        dp.callback_query.register(self.on_more_button, F.data.startswith("more_button_"))

    async def on_more_command(self, message: Message) -> None:
        rows: List[dict] = []
        if self.user_storage is not None:
            try:
                rows = await self.user_storage.get_more_buttons()
            except Exception as e:
                logger.warning("get_more_buttons: %s", e)

        if rows:
            await message.answer(
                _MORE_INTRO_LEGACY,
                reply_markup=_build_more_keyboard_db(rows),
            )
            return

        ik = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=f"preset_faq_{i}")]
                for i, (label, _) in enumerate(_PRESET_FAQ_ITEMS)
            ]
        )
        await message.answer(
            "<b>Частые запросы</b>\n\n"
            "Выберите тему — бот подставит формулировку и ответит.",
            reply_markup=ik,
            parse_mode=ParseMode.HTML,
        )

    async def on_preset_faq(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await _clear_support_if_active(state)

        data = callback.data or ""
        try:
            idx = int(data.replace("preset_faq_", "", 1))
        except ValueError:
            return
        if idx < 0 or idx >= len(_PRESET_FAQ_ITEMS):
            return
        _, prompt = _PRESET_FAQ_ITEMS[idx]
        await self._run_agent_prompt(callback, prompt, reply_to_message=None)

    async def on_more_button(self, callback: CallbackQuery, state: FSMContext) -> None:
        await _clear_support_if_active(state)

        data = callback.data or ""
        try:
            btn_id = int(data.replace("more_button_", "", 1))
        except ValueError:
            await callback.answer("❌ Ошибка")
            return

        if not self.user_storage:
            await callback.answer("❌ Недоступно")
            return

        row = await self.user_storage.get_button_by_id(btn_id)
        if not row:
            await callback.answer("❌ Тема не найдена")
            return

        button_text = str(row.get("button_text") or "").strip() or "тему"
        content = (row.get("content_text") or "").strip()
        prompt_to_agent = content or button_text

        await callback.answer(f"✅ Отправляю: {button_text[:200]}")

        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass

        uid = callback.from_user.id if callback.from_user else 0
        echo_msg = await callback.message.answer(button_text)

        try:
            await self.user_storage.log_message(uid, f"Button: {button_text}", "user")
        except Exception:
            pass

        await self._run_agent_prompt(callback, prompt_to_agent, reply_to_message=echo_msg)

    async def _run_agent_prompt(
        self,
        callback: CallbackQuery,
        prompt: str,
        *,
        reply_to_message: Optional[Message] = None,
    ) -> None:
        """``reply_to_message`` — сообщение, на которое вешаем ответ (после delete исходного из /more)."""
        anchor = reply_to_message if reply_to_message is not None else callback.message
        chat_id = callback.message.chat.id
        thread_id = callback.message.message_thread_id

        messaging = self.feature_manager.get_optional("messaging")
        if not messaging or not getattr(messaging, "agents_client", None):
            await anchor.answer("❌ Диалог временно недоступен.")
            return

        uid = callback.from_user.id if callback.from_user else 0
        tg = self.bot
        try:
            if tg:
                async with ChatActionSender.typing(
                    chat_id,
                    tg,
                    thread_id,
                ):
                    reply = await messaging.agents_client.run(
                        user_message=prompt, user_id=uid
                    )
            else:
                reply = await messaging.agents_client.run(
                    user_message=prompt, user_id=uid
                )
        except Exception as e:
            logger.error("more/agent: %s", e, exc_info=True)
            await anchor.answer("❌ Не удалось получить ответ. Попробуйте позже.")
            return

        if not reply:
            await anchor.answer("🙏 Сейчас не получилось сформулировать ответ.")
            return

        oc = messaging.agents_client if messaging else None
        text_out = await normalize_llm_reply_for_telegram_async(
            reply,
            user_id=uid,
            agents_client=oc,
        )
        try:
            await anchor.reply(
                text_out,
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as e:
            if "message to be replied not found" in (e.message or "").lower():
                await callback.bot.send_message(
                    chat_id,
                    text_out,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=thread_id,
                )
            else:
                raise
