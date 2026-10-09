"""Deep link /start finplan — PDF плана чтения «Библия и финансы»."""

from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.texts import media_file_ids as media_ids
from bot.texts import ru_bible_finance as txt

logger = logging.getLogger(__name__)


def configured_finplan_pdf_file_id() -> str:
    return (getattr(media_ids, "BIBLE_FINANCE_PDF_FILE_ID", "") or "").strip()


def build_finplan_deeplink(bot_username: str) -> str:
    username = (bot_username or "").lstrip("@")
    return f"https://t.me/{username}?start={txt.START_PARAM_FINPLAN}"


class BibleFinanceFeature(BaseFeature):
    name = "bible_finance"

    def __init__(self, user_storage, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager

    def register_handlers(self, _dp: Dispatcher) -> None:
        """Вход только через /start finplan в онбординге."""

    def _matches_start_param(self, param: str) -> bool:
        return (param or "").strip().lower() == txt.START_PARAM_FINPLAN

    async def try_open_from_start(self, message: Message, param: str) -> bool:
        if not self._matches_start_param(param):
            return False
        user = message.from_user
        if user is None or user.is_bot:
            return False
        fid = configured_finplan_pdf_file_id()
        if not fid:
            logger.warning("[%s] PDF file_id не задан", self.name)
            await message.answer(txt.SEND_FAILED_TEXT)
            return True
        try:
            await message.answer_document(
                document=fid,
                caption=txt.PDF_CAPTION,
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest as e:
            logger.warning("[%s] send pdf failed: %s", self.name, e.message)
            await message.answer(txt.SEND_FAILED_TEXT)
        except Exception as e:
            logger.warning("[%s] send pdf failed: %s", self.name, e)
            await message.answer(txt.SEND_FAILED_TEXT)
        logger.info("[%s] finplan delivered user=%s", self.name, user.id)
        return True
