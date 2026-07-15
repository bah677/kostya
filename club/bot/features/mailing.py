# bot/features/mailing.py
import logging
import asyncio
from typing import Dict, Optional, Set, Tuple, List, Any, cast

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo

from bot.features.base import BaseFeature
from bot.utils.telegram_identity import resolve_telegram_bot_username
from storage.mailing_storage import MailingStorage

logger = logging.getLogger(__name__)


class MailingFeature(BaseFeature):
    """
    Фича рассылок.
    Поддерживает текст, фото, видео, голосовые, видео-кружочки.
    """

    name = "mailing"

    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.mailing_storage = None
        self._processing_campaigns = set()
        self._campaign_tasks: Set[asyncio.Task] = set()
        self._is_running = False
        self._check_task = None

    async def initialize(self) -> None:
        """Инициализация фичи."""
        self.mailing_storage = MailingStorage(self.user_storage.db)
        await self.mailing_storage.recover_stale_mailing_state()

        self._is_running = True

        await self._check_ready_campaigns()

        self._check_task = asyncio.create_task(self._check_loop())

        logger.info(f"[{self.name}] Фича инициализирована")

    async def teardown(self) -> None:
        """Остановка фичи: цикл проверки, активные задачи кампаний."""
        self._is_running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        for t in list(self._campaign_tasks):
            if not t.done():
                t.cancel()
        if self._campaign_tasks:
            await asyncio.gather(*self._campaign_tasks, return_exceptions=True)

        await self.mailing_storage.close()
        logger.info(f"[{self.name}] Фича остановлена")

    def register_handlers(self, dp) -> None:
        """Команды/колбэки рассылки — во внешнем админ-боте."""
        pass

    def _spawn_campaign(self, campaign_id: int) -> None:
        t = asyncio.create_task(self._run_campaign(campaign_id))
        self._campaign_tasks.add(t)

        def _forget(_future):
            self._campaign_tasks.discard(t)

        t.add_done_callback(_forget)

    # ==================== ФОНОВАЯ ПРОВЕРКА ====================

    async def _check_loop(self):
        """Фоновый цикл проверки готовых кампаний."""
        logger.info("📡 Mailing check loop started, checking every 60 seconds")

        while self._is_running:
            try:
                await self._check_ready_campaigns()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in mailing check loop: {e}")
                await asyncio.sleep(60)

    async def _check_ready_campaigns(self):
        """Проверяет и запускает готовые кампании."""
        try:
            await self.mailing_storage.recover_stale_mailing_state()

            campaigns = await self.mailing_storage.get_ready_campaigns()

            for campaign in campaigns:
                campaign_id = campaign["id"]

                if campaign_id in self._processing_campaigns:
                    continue

                self._spawn_campaign(campaign_id)

        except Exception as e:
            logger.error(f"❌ Error checking ready campaigns: {e}")

    # ==================== ОТПРАВКА РАССЫЛКИ ====================

    async def _run_campaign(self, campaign_id: int):
        """Запускает и контролирует процесс рассылки."""
        if campaign_id in self._processing_campaigns:
            logger.warning(f"⚠️ Campaign {campaign_id} already in progress")
            return

        self._processing_campaigns.add(campaign_id)

        try:
            campaign = await self.mailing_storage.get_campaign(campaign_id)
            if not campaign or campaign["status"] != "planned":
                return

            total_users = await self.mailing_storage.get_audience_count(campaign_id)
            if total_users == 0:
                logger.warning(f"📭 No users in audience for campaign {campaign_id}")
                await self.mailing_storage.update_campaign_status(campaign_id, "completed")
                return

            await self.mailing_storage.update_campaign_status(campaign_id, "running")

            settings = await self.mailing_storage.get_mailing_settings()
            current_rate = settings.get("messages_per_second", 5)
            min_rate = settings.get("min_rate", 2)
            max_rate = settings.get("max_rate", 8)
            batch_size = settings.get("batch_size", 50)
            max_attempts = settings.get("max_attempts", 3)

            success_streak = 0
            consecutive_errors = 0

            sent_count = 0
            failed_count = 0
            blocked_count = 0

            logger.warning(
                "📤 Starting campaign %s: %s users, rate=%s msg/sec",
                campaign_id,
                total_users,
                current_rate,
            )

            while True:
                await self.mailing_storage.recover_stale_mailing_state()

                batch = await self.mailing_storage.claim_audience_batch(
                    campaign_id, batch_size
                )
                if not batch:
                    open_left = await self.mailing_storage.count_open_audience(
                        campaign_id
                    )
                    if open_left <= 0:
                        break
                    await asyncio.sleep(2)
                    continue

                logger.info("📦 Processing batch: size=%s", len(batch))

                for audience_item in batch:
                    audience_id = audience_item["id"]
                    user_id = audience_item["user_id"]

                    success, error_type = await self._send_to_user(
                        campaign,
                        user_id,
                        audience_id,
                        max_attempts,
                    )

                    if success:
                        sent_count += 1
                        success_streak += 1
                        consecutive_errors = 0
                    else:
                        if error_type == "blocked":
                            blocked_count += 1
                        elif error_type is not None:
                            failed_count += 1
                        success_streak = 0
                        consecutive_errors += 1

                    delay = 1.0 / current_rate
                    await asyncio.sleep(delay)

                    if success_streak > 50 and current_rate < max_rate:
                        current_rate = min(max_rate, current_rate + 1)
                        logger.debug("📈 Rate increased to %s msg/sec", current_rate)
                    elif consecutive_errors > 10 and current_rate > min_rate:
                        current_rate = max(min_rate, current_rate - 2)
                        logger.warning("📉 Rate decreased to %s msg/sec", current_rate)
                        consecutive_errors = 0

                await self.mailing_storage.update_campaign_status(
                    campaign_id,
                    "running",
                    sent_count,
                    failed_count,
                    blocked_count,
                )
                logger.info(
                    "📊 Progress: sent=%s, failed=%s, blocked=%s, rate=%s",
                    sent_count,
                    failed_count,
                    blocked_count,
                    current_rate,
                )

            await self.mailing_storage.update_campaign_status(
                campaign_id,
                "completed",
                sent_count,
                failed_count,
                blocked_count,
            )
            logger.warning(
                "✅ Campaign %s completed: sent=%s, failed=%s, blocked=%s",
                campaign_id,
                sent_count,
                failed_count,
                blocked_count,
            )

        except Exception as e:
            logger.error(f"❌ Campaign {campaign_id} failed: {e}", exc_info=True)
            await self.mailing_storage.update_campaign_status(campaign_id, "failed")
        finally:
            self._processing_campaigns.discard(campaign_id)

    async def _send_to_user(
        self,
        campaign: Dict,
        user_id: int,
        audience_id: int,
        max_attempts: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Отправляет сообщение пользователю.

        Строка аудитории остаётся в ``processing``, пока нет финального статуса
        (антидубль между воркерами).

        Returns:
            ``(success, error_type)`` где error_type: None при успехе, ``'blocked'``,
            ``'failed'``, или ``None`` при промежуточном ретрае (счётчики кампании не режем).
        """
        for attempt in range(max_attempts):
            await self.mailing_storage.touch_processing_lease(audience_id)
            try:
                text = campaign["text"]
                if campaign.get("has_ref_link"):
                    bot_username = await resolve_telegram_bot_username(self.bot)
                    if bot_username:
                        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
                        text = f"{text}\n\n{ref_link}"
                    else:
                        logger.warning(
                            "Campaign has_ref_link but bot username unresolved; "
                            "omitting ref link for user_id=%s",
                            user_id,
                        )

                keyboard = None
                buttons = campaign.get("buttons", [])
                if buttons:
                    keyboard_buttons = []
                    for btn in buttons:
                        if btn.get("url"):
                            button = InlineKeyboardButton(
                                text=btn["text"],
                                url=btn["url"],
                            )
                            if btn.get("style"):
                                button.style = btn["style"]
                            keyboard_buttons.append([button])
                        elif btn.get("callback"):
                            button = InlineKeyboardButton(
                                text=btn["text"],
                                callback_data=btn["callback"],
                            )
                            if btn.get("style"):
                                button.style = btn["style"]
                            keyboard_buttons.append([button])

                    if keyboard_buttons:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

                parse_mode = campaign.get("parse_mode", ParseMode.HTML)
                await self._send_campaign_bundle_to_user(
                    user_id=user_id,
                    body_text=text,
                    parse_mode=parse_mode,
                    keyboard=keyboard,
                    attachments=self._effective_campaign_attachments(campaign),
                )

                await self.mailing_storage.update_audience_status(
                    audience_id, "sent", attempt_count=attempt + 1
                )
                return True, None

            except Exception as e:
                error_msg = str(e).lower()

                if "429" in error_msg or "too many requests" in error_msg:
                    wait_time = 5 * (attempt + 1)
                    logger.warning(
                        "⏳ Flood control, waiting %ss for user %s",
                        wait_time,
                        user_id,
                    )
                    await self.mailing_storage.touch_processing_lease(audience_id)
                    await asyncio.sleep(wait_time)
                    continue

                if any(
                    word in error_msg
                    for word in ["forbidden", "blocked", "chat not found", "deactivated"]
                ):
                    logger.info("🚫 User %s blocked bot", user_id)
                    await self.mailing_storage.update_audience_status(
                        audience_id,
                        "blocked",
                        error=f"User blocked bot (attempt {attempt + 1})",
                        attempt_count=attempt + 1,
                    )
                    await self._deactivate_user(user_id)
                    return False, "blocked"

                logger.error(
                    "❌ Failed to send to user %s (attempt %s): %s",
                    user_id,
                    attempt + 1,
                    e,
                )
                if attempt == max_attempts - 1:
                    await self.mailing_storage.update_audience_status(
                        audience_id, "failed", error=str(e), attempt_count=attempt + 1
                    )
                    return False, "failed"

                await self.mailing_storage.update_audience_status(
                    audience_id,
                    "processing",
                    error=f"Retry {attempt + 1}: {str(e)}",
                    attempt_count=attempt + 1,
                )
                await asyncio.sleep(2)

        return False, "failed"

    @staticmethod
    def _effective_campaign_attachments(campaign: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
        raw = campaign.get("attachments")
        normalized: List[Dict[str, str]] = []
        if isinstance(raw, list):
            for it in raw:
                if not isinstance(it, dict):
                    continue
                t = str(it.get("type") or "").strip().lower()
                fid = str(it.get("file_id") or "").strip()
                if t and fid:
                    normalized.append({"type": t, "file_id": fid})
        if normalized:
            return normalized

        mt = str(campaign.get("media_type") or "").strip().lower()
        fid = campaign.get("media_file_id")
        if mt and fid:
            return [{"type": mt, "file_id": str(fid)}]
        return None

    @staticmethod
    def _resolve_parse_mode(parse_mode) -> ParseMode:
        if isinstance(parse_mode, ParseMode):
            return parse_mode
        sul = str(parse_mode or ParseMode.HTML).strip().upper().replace(" ", "")
        if sul in {"MARKDOWNV2"} or "MARKDOWN_V2" in sul:
            return ParseMode.MARKDOWN_V2
        if "MARKDOWN" in sul:
            return ParseMode.MARKDOWN_V2
        return ParseMode.HTML

    @staticmethod
    def _split_parcels(attachments: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        album_types = frozenset({"photo", "video"})
        parcels: List[Dict[str, Any]] = []
        i = 0
        while i < len(attachments):
            t = attachments[i]["type"]
            if t in album_types:
                j = i + 1
                while (
                    j < len(attachments)
                    and attachments[j]["type"] in album_types
                    and j - i < 10
                ):
                    j += 1
                parcels.append({"kind": "album", "items": attachments[i:j]})
                i = j
            else:
                parcels.append({"kind": "single", "items": [attachments[i]]})
                i += 1
        return parcels

    @staticmethod
    def _parcel_supports_caption(parcel: Dict[str, Any]) -> bool:
        if parcel["kind"] == "album":
            return True
        return parcel["items"][0]["type"] != "video_note"

    @staticmethod
    def _take_text_chunk(text: str, limit: int) -> Tuple[Optional[str], str]:
        if not text:
            return None, ""
        chunk = text[:limit]
        rest = text[limit:]
        return (chunk or None), rest

    async def _send_text_parts(
        self,
        *,
        user_id: int,
        text: str,
        parse_mode: ParseMode,
        keyboard: Optional[InlineKeyboardMarkup],
        msg_limit: int = 4096,
    ) -> None:
        zwnb = "\u2060"
        rest = (text or "").strip()
        parts: List[str] = []
        while rest:
            parts.append(rest[:msg_limit])
            rest = rest[msg_limit:]
        if not parts and keyboard:
            parts = [zwnb]
        for idx, part in enumerate(parts):
            await self.bot.send_message(
                chat_id=user_id,
                text=part,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
                reply_markup=keyboard if idx == len(parts) - 1 else None,
            )

    async def _send_campaign_bundle_to_user(
        self,
        *,
        user_id: int,
        body_text: str,
        parse_mode: str,
        keyboard: Optional[InlineKeyboardMarkup],
        attachments: Optional[List[Dict[str, str]]],
    ) -> None:
        pm = self._resolve_parse_mode(parse_mode)
        text = cast(str, body_text or "").strip()

        if not attachments:
            await self._send_text_parts(
                user_id=user_id,
                text=text,
                parse_mode=pm,
                keyboard=keyboard,
            )
            return

        parcels = self._split_parcels(attachments)
        use_caption = len(parcels) == 1 and self._parcel_supports_caption(parcels[0])
        remaining = text
        caption_text: Optional[str] = None
        if use_caption and remaining:
            caption_text, remaining = self._take_text_chunk(remaining, 1024)

        for parcel_idx, parcel in enumerate(parcels):
            cap = caption_text if parcel_idx == 0 and use_caption else None
            if parcel["kind"] == "album":
                medias: List[InputMediaPhoto | InputMediaVideo] = []
                for idx, item in enumerate(parcel["items"]):
                    item_cap = cap if idx == 0 else None
                    if item["type"] == "photo":
                        medias.append(
                            InputMediaPhoto(
                                media=item["file_id"],
                                caption=item_cap,
                                parse_mode=pm if item_cap else None,
                            )
                        )
                    else:
                        medias.append(
                            InputMediaVideo(
                                media=item["file_id"],
                                caption=item_cap,
                                parse_mode=pm if item_cap else None,
                            )
                        )
                await self.bot.send_media_group(chat_id=user_id, media=medias)
                continue

            it = parcel["items"][0]
            lt = it["type"]
            if lt == "voice":
                await self.bot.send_voice(
                    chat_id=user_id,
                    voice=it["file_id"],
                    caption=cap,
                    parse_mode=pm if cap else None,
                )
            elif lt == "video_note":
                await self.bot.send_video_note(
                    chat_id=user_id,
                    video_note=it["file_id"],
                )
            elif lt == "animation":
                await self.bot.send_animation(
                    chat_id=user_id,
                    animation=it["file_id"],
                    caption=cap,
                    parse_mode=pm if cap else None,
                )
            elif lt == "document":
                await self.bot.send_document(
                    chat_id=user_id,
                    document=it["file_id"],
                    caption=cap,
                    parse_mode=pm if cap else None,
                )
            else:
                logger.warning(
                    "mailing attachment type '%s' → send_photo fallback",
                    lt,
                )
                await self.bot.send_photo(
                    chat_id=user_id,
                    photo=it["file_id"],
                    caption=cap,
                    parse_mode=pm if cap else None,
                )

        if not use_caption and text:
            remaining = text
        await self._send_text_parts(
            user_id=user_id,
            text=remaining,
            parse_mode=pm,
            keyboard=keyboard,
        )

    async def _deactivate_user(self, user_id: int):
        """Помечает пользователя неактивным после блокировки бота."""
        try:
            await self.mailing_storage.deactivate_user(user_id)
        except Exception as e:
            logger.error(f"❌ Failed to deactivate user {user_id}: {e}")
