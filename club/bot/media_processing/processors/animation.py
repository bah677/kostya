"""Обработчик GIF/анимаций — описание через Vision API."""

import base64
import logging
from typing import Any, Dict, Optional

import aiofiles

from bot.media_processing.models import MediaType, ProcessedMedia
from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.processors.photo import PhotoProcessor
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class AnimationProcessor(BaseProcessor):
    """GIF и короткие анимации — описание кадра через Vision (как фото)."""

    def __init__(self, user_storage, openai_client: OpenAIClient):
        super().__init__(user_storage)
        self._photo = PhotoProcessor(user_storage, openai_client)

    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        return file_info.get("file_type") == "animation"

    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any],
    ) -> ProcessedMedia:
        if not file_path:
            return ProcessedMedia(
                text="[анимация (не удалось скачать)]",
                media_type=MediaType.UNKNOWN,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=0,
                file_id=file_info.get("file_id"),
            )

        photo_info = dict(file_info)
        photo_info["file_type"] = "photo"
        result = await self._photo.process(file_path, user_id, photo_info)
        if result.text and result.text.startswith("[фото:"):
            result.text = result.text.replace("[фото:", "[анимация:", 1)
        elif result.text and result.text.startswith("[фото "):
            result.text = result.text.replace("[фото ", "[анимация ", 1)
        result.media_type = MediaType.UNKNOWN
        result.metadata = {**(result.metadata or {}), "original_type": "animation"}
        return result
