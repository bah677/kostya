"""Диалог с агентом: DeepSeek + история из messages.

Поверх базового MessagingFeature — условные кнопки «Поддержать проект» /
реферальная ссылка на клубного бота (см. _send_to_user).
"""

import logging

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from bot.features.messaging import MessagingFeature
from bot.utils.donation_reply import maybe_donation_keyboard
from bot.utils.telegram_html import (
    balance_telegram_html_tags,
    html_to_plain,
    sanitize_telegram_html,
    split_telegram_html_message_chunks,
    strip_subscribe_cta,
)
from bot.utils.telegram_html_async import normalize_llm_reply_for_telegram_async

logger = logging.getLogger(__name__)

_HTML_CHUNK_LEN = 3500


class ScriptureMessagingFeature(MessagingFeature):
    """MessagingFeature с системным промптом Писания; донат/клуб — по флагам и вероятностям."""

    def register_handlers(self, dp: Dispatcher) -> None:
        """Точки входа — общие MessageHandlers."""

    async def _reply_html(
        self,
        message: Message,
        html_text: str,
        *,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> None:
        safe = balance_telegram_html_tags(sanitize_telegram_html(html_text))
        chunks = split_telegram_html_message_chunks(safe, max_len=_HTML_CHUNK_LEN) or [""]

        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            kb = keyboard if is_last else None
            try:
                await message.reply(chunk, parse_mode=ParseMode.HTML, reply_markup=kb)
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "message is too long" in err:
                    subs = split_telegram_html_message_chunks(chunk, max_len=3000)
                    for j, sub in enumerate(subs):
                        await message.reply(
                            sub,
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb if (is_last and j == len(subs) - 1) else None,
                        )
                    continue
                if "can't parse entities" in err or "can't find end tag" in err:
                    plain = html_to_plain(chunk)[:4096]
                    await message.reply(plain, reply_markup=kb)
                    continue
                raise

    async def _send_to_user(self, message: Message, response: str) -> None:
        try:
            uid = message.from_user.id if message.from_user else 0

            body, _ = strip_subscribe_cta(response)
            oc = self.agents_client
            text_out = await normalize_llm_reply_for_telegram_async(
                body,
                user_id=uid,
                agents_client=oc,
            )

            keyboard, donation_variant = await maybe_donation_keyboard(
                self.user_storage, uid
            )

            await self._reply_html(message, text_out, keyboard=keyboard)
            logger.info(
                "✅ Ответ пользователю %s отправлен (donation_keyboard=%s variant=%s)",
                message.from_user.id,
                keyboard is not None,
                donation_variant,
            )
        except Exception as e:
            logger.error("❌ Не удалось отправить ответ: %s", e)
