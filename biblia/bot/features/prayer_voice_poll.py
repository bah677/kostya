"""Опрос голосов молитвы: образцы + оценки 1–5."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.services.elevenlabs_tts import ElevenLabsTTS
from bot.services.prayer_bg_music import mix_voice_with_bg_music
from bot.services.prayer_tts_style import (
    audio_bytes_to_ogg_opus,
    resolve_prayer_tts_atempo,
)
from bot.services.tg_voice_delivery import prepare_ogg_bytes, send_tg_audio_payload
from bot.services.prayer_voice_poll import (
    POLL_SLUG,
    PRAYER_VOICE_POLL_SAMPLE,
    build_poll_voices,
    build_rating_keyboard,
    format_poll_intro_html,
    format_poll_scores_html,
    parse_rating_callback,
)
from bot.services.voicebox_tts import format_prayer_for_tts
from config import config

logger = logging.getLogger(__name__)


class PrayerVoicePollFeature(BaseFeature):
    name = "prayer_voice_poll"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot: Optional[Bot] = None
        self._tts = ElevenLabsTTS()

    def set_bot(self, app: Any) -> None:
        self.bot = app.bot if app is not None else None

    async def initialize(self) -> None:
        await self.user_storage.ensure_prayer_voice_poll_schema()
        logger.info("[%s] schema ok", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.cmd_scores, Command("prayer_voice_scores"))
        dp.message.register(self.cmd_send_poll, Command("prayer_voice_poll"))
        dp.callback_query.register(self.on_rating, F.data.startswith("pvp:"))
        logger.info("[%s] /prayer_voice_scores /prayer_voice_poll + pvp:*", self.name)

    async def _ensure_admin(self, uid: Optional[int]) -> bool:
        if not uid:
            return False
        return await is_admin_or_super(self.user_storage, uid)

    async def cmd_scores(self, message: Message) -> None:
        if not message.from_user or not await self._ensure_admin(message.from_user.id):
            return
        await self._reply_scores(message)

    async def cmd_send_poll(self, message: Message) -> None:
        """Повторно прислать опрос вызвавшему админу (тест / догон)."""
        if not message.from_user or not await self._ensure_admin(message.from_user.id):
            return
        wait = await message.answer("⏳ Готовлю образцы голосов (~40 с каждый)…")
        try:
            await self.send_poll_to_user(message.from_user.id)
            await wait.edit_text("✅ Образцы отправлены. Оцените каждый голос кнопками 1–5.")
        except Exception as e:
            logger.exception("[%s] send poll: %s", self.name, e)
            await wait.edit_text(f"❌ Ошибка: {e}")

    async def on_rating(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not uid:
            await callback.answer("?")
            return
        parsed = parse_rating_callback(callback.data or "")
        if not parsed:
            await callback.answer("?")
            return
        voice_idx, score = parsed
        if score < 1 or score > 5:
            await callback.answer("?")
            return
        voices = build_poll_voices(config.ELEVENLABS_VOICE_ID)
        voice = next((v for v in voices if int(v["idx"]) == voice_idx), None)
        if not voice:
            await callback.answer("Голос не найден", show_alert=True)
            return
        await self.user_storage.upsert_prayer_voice_rating(
            poll_slug=POLL_SLUG,
            admin_user_id=uid,
            voice_id=str(voice["voice_id"]),
            voice_idx=voice_idx,
            score=score,
        )
        kb = build_rating_keyboard(voice_idx, selected=score)
        try:
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception as e:
            logger.debug("[%s] edit markup: %s", self.name, e)
        await callback.answer(f"Оценка {score} сохранена")

    async def _reply_scores(self, message: Message) -> None:
        voices = build_poll_voices(config.ELEVENLABS_VOICE_ID)
        agg = await self.user_storage.list_prayer_voice_ratings_aggregate(POLL_SLUG)
        stats = await self.user_storage.get_prayer_voice_poll_participant_stats(
            POLL_SLUG,
            voice_count=len(voices),
        )
        text = format_poll_scores_html(
            voices,
            agg,
            poll_slug=POLL_SLUG,
            participant_stats=stats,
        )
        await message.answer(text, parse_mode=ParseMode.HTML)

    async def _synthesize_voice(self, voice_id: str, tts_text: str) -> Optional[bytes]:
        if not self._tts.api_key:
            return None
        tempo = resolve_prayer_tts_atempo()
        raw_mp3 = await self._tts.synthesize_ogg_opus(
            tts_text, voice_id=voice_id, as_ogg=False
        )
        mixed = await asyncio.to_thread(
            mix_voice_with_bg_music,
            raw_mp3,
            atempo=tempo,
            voice_suffix=".mp3",
        )
        if mixed:
            return mixed
        return await asyncio.to_thread(
            audio_bytes_to_ogg_opus,
            raw_mp3,
            atempo=tempo,
            prefix="elabs_poll_",
        )

    async def synthesize_all(self) -> Dict[int, bytes]:
        tts_text = format_prayer_for_tts(PRAYER_VOICE_POLL_SAMPLE)
        voices = build_poll_voices(config.ELEVENLABS_VOICE_ID)
        out: Dict[int, bytes] = {}
        for v in voices:
            idx = int(v["idx"])
            vid = str(v["voice_id"])
            logger.info("[%s] synthesize idx=%s voice=%s", self.name, idx, vid[:8])
            try:
                ogg = await self._synthesize_voice(vid, tts_text)
                if ogg:
                    out[idx] = ogg
            except Exception as e:
                logger.error("[%s] synth idx=%s: %s", self.name, idx, e)
        return out

    def poll_cache_dir(self) -> Path:
        return (
            Path(__file__).resolve().parents[2]
            / "data"
            / "prayer_voice_poll_cache"
            / POLL_SLUG
        )

    def load_cached_audio(self) -> Optional[Dict[int, bytes]]:
        voices = build_poll_voices(config.ELEVENLABS_VOICE_ID)
        cache = self.poll_cache_dir()
        out: Dict[int, bytes] = {}
        for v in voices:
            idx = int(v["idx"])
            path = cache / f"voice_poll_{idx}.ogg"
            if not path.is_file():
                return None
            out[idx] = path.read_bytes()
        return out if len(out) == len(voices) else None

    def save_cached_audio(self, audio_by_idx: Dict[int, bytes]) -> Path:
        cache = self.poll_cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        for idx, data in audio_by_idx.items():
            (cache / f"voice_poll_{idx}.ogg").write_bytes(data)
        return cache

    async def resolve_poll_audio(
        self, audio_by_idx: Optional[Dict[int, bytes]] = None
    ) -> Dict[int, bytes]:
        if audio_by_idx is not None:
            return audio_by_idx
        cached = self.load_cached_audio()
        if cached:
            return cached
        audio = await self.synthesize_all()
        if audio:
            self.save_cached_audio(audio)
        return audio

    async def send_poll_to_user(
        self,
        user_id: int,
        *,
        audio_by_idx: Optional[Dict[int, bytes]] = None,
    ) -> None:
        if not self.bot:
            raise RuntimeError("bot not set")
        voices = build_poll_voices(config.ELEVENLABS_VOICE_ID)
        audio_by_idx = await self.resolve_poll_audio(audio_by_idx)
        existing = await self.user_storage.list_prayer_voice_ratings_by_admin(
            POLL_SLUG, user_id
        )
        n = len(voices)
        await self.bot.send_message(
            user_id,
            format_poll_intro_html(n),
            parse_mode=ParseMode.HTML,
        )
        total = len(voices)
        for v in voices:
            idx = int(v["idx"])
            ogg = audio_by_idx.get(idx)
            title = str(v["title"])
            vid = str(v["voice_id"])
            selected = existing.get(vid)
            caption = f"{idx + 1}/{total}. {title}"
            kb = build_rating_keyboard(idx, selected=selected)
            if not ogg:
                await self.bot.send_message(
                    user_id,
                    f"<i>{idx + 1}/{total}. {title} — не удалось озвучить</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                continue
            payload = prepare_ogg_bytes(ogg, filename_base=f"voice_poll_{idx}")
            if not payload:
                await self.bot.send_message(
                    user_id,
                    f"<i>{idx + 1}/{total}. {title} — не удалось подготовить аудио</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                continue
            kw: dict[str, Any] = {
                "caption": caption[:1024],
                "parse_mode": ParseMode.HTML,
                "reply_markup": kb,
            }
            await send_tg_audio_payload(
                user_id,
                payload,
                bot=self.bot,
                **kw,
            )

    async def send_poll_to_all_admins(self) -> int:
        if not self.bot:
            raise RuntimeError("bot not set")
        admin_ids: set[int] = set()
        sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
        if sid > 0:
            admin_ids.add(sid)
        for row in await self.user_storage.list_telegram_admin_ids():
            tid = int(row.get("telegram_user_id") or 0)
            if tid > 0:
                admin_ids.add(tid)
        audio = await self.resolve_poll_audio()
        sent = 0
        for uid in sorted(admin_ids):
            try:
                await self.send_poll_to_user(uid, audio_by_idx=audio)
                sent += 1
            except Exception as e:
                logger.error("[%s] send poll uid=%s: %s", self.name, uid, e)
        return sent
