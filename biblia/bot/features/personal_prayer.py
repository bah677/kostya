"""Команда /prayer: свободный рассказ → до 2 уточнений → молитва + голос."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.services.elevenlabs_tts import ElevenLabsTTS
from bot.services.openai_tts import OpenAIPrayerTTS
from bot.services.prayer_stress import (
    PrayerStressWord,
    build_prayer_stress_sample_text,
    parse_prayer_stress_words,
)
from bot.services.prayer_bg_music import mix_voice_with_bg_music
from bot.services.prayer_tts_style import resolve_prayer_tts_atempo
from bot.services.prayer_tts_queue import PrayerTtsQueue, get_prayer_tts_queue
from bot.services.prayer_rag import build_compose_user_content, fetch_prayer_style_examples
from bot.services.salute_tts import SaluteSpeechTTS
from bot.services.voicebox_tts import VoiceboxPrayerTTS, format_prayer_for_tts
from bot.services.yandex_speechkit import YandexSpeechKitTTS
from bot.states import PrayerStates
from bot.utils.admin_channel import admin_channel_chat_id
from bot.utils.chat_actions import record_voice_chat_action
from config import config
from openai_client.agents_client import AgentsClient
from openai_client.prayer_prompt import (
    PRAYER_INTAKE_SYSTEM_PROMPT,
    pick_prayer_compose_variant,
    prayer_compose_max_tokens,
    prayer_compose_request_kind,
    resolve_prayer_compose_system_prompt,
)

logger = logging.getLogger(__name__)

_MAX_CLARIFY = 2
_TG_CAPTION_MAX = 1024
_TG_MESSAGE_MAX = 4096
_CANCEL_WORDS = frozenset(
    {"отмена", "отменить", "стоп", "cancel", "/cancel"}
)
_PRAYER_STRESS_OPEN_CB = "prayer_stress_open"
_PRAYER_STRESS_DECIDE_PREFIX = "pst:"

# Текст подписи к голосовому (инструкция). В TTS уходит текст молитвы, не это.
_PRAYER_VOICE_CAPTION = (
    "Найди тихое место. Закрой глаза.\n\n"
    "Повторяй слова молитвы про себя или совсем тихо вслух — "
    "собери всё внимание внутрь себя, в каждое произносимое слово.\n\n"
    "Не спеши. Пусть сердце услышит.\n\n"
    "И ещё: поставь воспроизведение на скорость 1× — "
    "так молитва звучит спокойнее и глубже."
)

_PRAYER_DONATION_FOOTER_HTML = (
    "Генерация голоса требует много ресурсов и стоит довольно дорого, "
    "поэтому каждое ваше пожертвование помогает нам сохранять и развивать "
    "эту функцию.\n\n"
    "<blockquote>Носите бремена друг друга, и таким образом исполните закон Христов.</blockquote>\n"
    "<i>Гал. 6:2</i>"
)


@dataclass(frozen=True)
class _PrayerStressProposalContext:
    proposal_id: int
    source_word: str
    base_word: str
    accented_word: str
    sample_text: str


_COMPARE_ENGINE_ORDER = (
    "openai",
    "yandex",
    "voicebox",
    "salute",
    "elevenlabs",
)


class _TTS(Protocol):
    @property
    def configured(self) -> bool: ...

    async def synthesize_ogg_opus(self, text: str) -> bytes: ...


def _first_compare_ogg(voices: Optional[dict[str, Optional[bytes]]]) -> Optional[bytes]:
    if not voices:
        return None
    # Сначала пробуем ключи без префикса варианта (старый формат), потом A:/B:.
    for key in _COMPARE_ENGINE_ORDER:
        if key == "elevenlabs":
            continue
        ogg = voices.get(key) or voices.get(f"A:{key}") or voices.get(f"B:{key}")
        if ogg:
            return ogg
    for key, ogg in voices.items():
        if ogg and ("elevenlabs:" in key):
            return ogg
    for ogg in voices.values():
        if ogg:
            return ogg
    return None


def _clean_llm_prayer_text(raw: str) -> str:
    """Исходный текст от LLM для показа пользователю (без TTS-форматирования)."""
    t = (raw or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip().strip('"').strip("«»")


def _strip_prayer_text(raw: str) -> str:
    """Совместимость: лёгкая очистка без словаря ударений."""
    return _clean_llm_prayer_text(raw)


def _is_voice_forbidden_error(exc: Exception) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    text = str(exc)
    return "VOICE_MESSAGES_FORBIDDEN" in text or "voice messages forbidden" in text.lower()


def _format_user_context(turns: List[str]) -> str:
    lines: List[str] = []
    for i, t in enumerate(turns, 1):
        lines.append(f"Сообщение пользователя #{i}:\n{t}")
    return "\n\n".join(lines)


def _parse_intake(raw: Optional[str]) -> dict[str, Any]:
    """Разобрать ответ intake. При сбое — ready (не мучить лишними вопросами)."""
    if not raw:
        return {"action": "ready"}
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            action = str(data.get("action") or "").strip().lower()
            if action == "ask":
                q = str(data.get("question") or "").strip()
                if q:
                    return {"action": "ask", "question": q}
            return {"action": "ready"}
    except json.JSONDecodeError:
        pass
    # Иногда модель пишет JSON внутри текста
    m = re.search(r"\{[^{}]*\}", text, flags=re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and str(data.get("action") or "").lower() == "ask":
                q = str(data.get("question") or "").strip()
                if q:
                    return {"action": "ask", "question": q}
        except json.JSONDecodeError:
            pass
    return {"action": "ready"}


class PersonalPrayerFeature(BaseFeature):
    name = "personal_prayer"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot: Optional[Bot] = None
        self.agents_client: Optional[AgentsClient] = None
        self.voicebox = VoiceboxPrayerTTS()
        self.speechkit = YandexSpeechKitTTS()
        self.openai_tts = OpenAIPrayerTTS()
        self.salute_tts = SaluteSpeechTTS()
        self.elevenlabs_tts = ElevenLabsTTS()
        self.tts_queue: PrayerTtsQueue = get_prayer_tts_queue(
            max_concurrent=int(getattr(config, "PRAYER_TTS_MAX_CONCURRENT", 1) or 1)
        )
        self._stress_dict_cache: dict[str, str] = {}

    @property
    def tts(self) -> _TTS:
        if self.voicebox.configured:
            return self.voicebox
        return self.speechkit

    def set_bot(self, app) -> None:
        self.bot = app.bot if app is not None else None

    async def initialize(self) -> None:
        self.agents_client = AgentsClient(self.user_storage)
        await self.user_storage.ensure_prayer_stress_schema()
        self._stress_dict_cache = await self.user_storage.get_prayer_stress_dictionary()
        if self.voicebox.configured:
            logger.info(
                "[%s] Voicebox TTS готов (profile=%s atempo=%s queue_max=%s)",
                self.name,
                self.voicebox.profile_id[:8],
                self.voicebox.atempo,
                self.tts_queue.max_concurrent,
            )
        elif self.speechkit.configured:
            logger.info(
                "[%s] SpeechKit готов (voice=%s) — Voicebox выключен",
                self.name,
                self.speechkit.voice,
            )
        else:
            logger.warning(
                "[%s] TTS не настроен (Voicebox/SpeechKit) — только текст молитвы",
                self.name,
            )
        if self.elevenlabs_tts.configured and self.elevenlabs_tts.voice_id:
            logger.info(
                "[%s] ElevenLabs TTS готов (voice=%s… queue_max=%s)",
                self.name,
                self.elevenlabs_tts.voice_id[:8],
                self.tts_queue.max_concurrent,
            )
        if self.openai_tts.configured:
            logger.info(
                "[%s] OpenAI TTS (model=%s voice=%s)",
                self.name,
                self.openai_tts.model,
                self.openai_tts.voice,
            )
        if self.salute_tts.configured:
            logger.info("[%s] SaluteSpeech TTS готов", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.on_prayer_command, Command(commands=["prayer", "molitva"]))
        dp.callback_query.register(
            self.on_prayer_callback,
            F.data.in_({"prayer_start", "molitva_start"}),
        )
        dp.callback_query.register(
            self.on_prayer_stress_open,
            F.data == _PRAYER_STRESS_OPEN_CB,
        )
        dp.callback_query.register(
            self.on_prayer_stress_moderation,
            F.data.startswith(_PRAYER_STRESS_DECIDE_PREFIX),
        )
        logger.info("[%s] Команды /prayer /molitva + callback prayer_start", self.name)

    async def on_prayer_command(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        await self._start_prayer(message, state, args=(command.args or "").strip())

    async def on_prayer_callback(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        """Кнопка рассылки: callback_data=prayer_start → как /prayer."""
        await callback.answer()
        if not callback.message:
            return
        await self._start_prayer(callback.message, state, args="")

    async def _start_prayer(
        self, message: Message, state: FSMContext, *, args: str = ""
    ) -> None:
        await state.clear()
        await state.set_state(PrayerStates.collecting)
        await state.update_data(prayer_turns=[], clarify_count=0)

        if args:
            await self._on_user_turn(message, state, args)
            return

        await message.answer(
            "<b>🙏 Персональная молитва</b>\n\n"
            "Расскажите своими словами, что у вас на сердце — "
            "о чём хотите помолиться.\n"
            "Можно сразу всё в одном сообщении: я сам пойму акцент молитвы "
            "и обращусь к Небесному Отцу.\n\n"
            "<i>Если чего-то не хватит — задам не больше двух коротких "
            "уточнений. Отмена: «отмена» или снова /prayer</i>",
            parse_mode=ParseMode.HTML,
        )

    async def handle_message(self, message: Message, state: FSMContext, text: str) -> None:
        cur = await state.get_state()
        if not cur or "PrayerStates" not in str(cur):
            return
        if cur.endswith("generating"):
            return

        content = (text or "").strip()
        if content.lower() in _CANCEL_WORDS:
            await state.clear()
            await message.answer("Молитва отменена. Когда будете готовы — снова /prayer")
            return
        if not content:
            await message.answer("Напишите текстом, что у вас на сердце — или «отмена».")
            return

        if cur.endswith("waiting_stress_feedback"):
            await state.clear()
            await message.answer(
                "Поправка ударений отключена. Чтобы получить молитву — /prayer"
            )
            return

        await self._on_user_turn(message, state, content)

    async def _on_user_turn(
        self, message: Message, state: FSMContext, content: str
    ) -> None:
        data = await state.get_data()
        turns: List[str] = list(data.get("prayer_turns") or [])
        clarify_count = int(data.get("clarify_count") or 0)
        turns.append(content)
        await state.update_data(prayer_turns=turns)

        uid = message.from_user.id if message.from_user else 0

        # Уже исчерпали лимит уточнений — сразу молитва по всему контексту.
        if clarify_count >= _MAX_CLARIFY:
            await self._generate_and_send(message, state, turns)
            return

        decision = await self._intake_decision(uid, turns)
        if decision.get("action") == "ask" and clarify_count < _MAX_CLARIFY:
            question = str(decision.get("question") or "").strip()
            if question:
                await state.update_data(clarify_count=clarify_count + 1)
                await message.answer(html.escape(question))
                return

        await self._generate_and_send(message, state, turns)

    async def _intake_decision(
        self, user_id: int, turns: List[str]
    ) -> dict[str, Any]:
        if not self.agents_client:
            return {"action": "ready"}
        raw = await self.agents_client.complete(
            system_prompt=PRAYER_INTAKE_SYSTEM_PROMPT,
            user_content=_format_user_context(turns),
            user_id=user_id,
            request_kind="personal_prayer_intake",
            temperature=0.2,
            max_tokens=250,
        )
        decision = _parse_intake(raw)
        logger.info(
            "[%s] intake uid=%s turns=%s action=%s",
            self.name,
            user_id,
            len(turns),
            decision.get("action"),
        )
        return decision

    async def _generate_and_send(
        self,
        message: Message,
        state: FSMContext,
        turns: List[str],
    ) -> None:
        uid = message.from_user.id if message.from_user else 0
        await state.set_state(PrayerStates.generating)

        wait_msg = await message.answer(
            "⏳ Составляю молитву и готовлю голосовое сообщение…"
        )

        bot = self.bot
        prayer_text: Optional[str] = None
        ogg: Optional[bytes] = None

        try:
            if bot:
                async with record_voice_chat_action(
                    bot, message.chat.id, message_thread_id=message.message_thread_id
                ):
                    prayer_text, ogg = await self._compose_and_synthesize(
                        uid, turns, wait_msg=wait_msg
                    )
            else:
                prayer_text, ogg = await self._compose_and_synthesize(
                    uid, turns, wait_msg=wait_msg
                )

            if not prayer_text:
                await wait_msg.edit_text(
                    "Не удалось составить молитву. Попробуйте позже или /prayer снова."
                )
                return

            try:
                await wait_msg.delete()
            except Exception:
                pass

            await self._deliver_prayer(message, bot, prayer_text, ogg)
            logger.info(
                "[%s] prayer delivered uid=%s voice=%s",
                self.name,
                uid,
                bool(ogg),
            )
            if uid and await is_admin_or_super(self.user_storage, uid):
                await self._deliver_admin_tts_compare(
                    message, bot, prayer_text
                )
        except Exception as e:
            logger.error("[%s] generate failed uid=%s: %s", self.name, uid, e, exc_info=True)
            try:
                await wait_msg.edit_text(
                    "Не удалось подготовить молитву. Попробуйте позже или /prayer снова."
                )
            except Exception:
                pass
        finally:
            await state.clear()

    async def _notify_tts_queue(
        self, wait_msg: Optional[Message], ahead: int
    ) -> None:
        if wait_msg is None or ahead <= 0:
            return
        try:
            await wait_msg.edit_text(
                "Сейчас много обращений, и генерация голоса занимает время.\n"
                f"Пожалуйста, подождите — ваша молитва в очереди "
                f"(перед вами примерно {ahead})."
            )
        except Exception as e:
            logger.debug("[%s] queue wait notify failed: %s", self.name, e)

    async def _compose_and_synthesize(
        self,
        uid: int,
        turns: List[str],
        *,
        wait_msg: Optional[Message] = None,
    ) -> tuple[Optional[str], Optional[bytes]]:
        """Промпт B → озвучка ТЕКСТА МОЛИТВЫ (ElevenLabs #1 + фон). Подпись = инструкция."""
        logger.info("[%s] compose start uid=%s turns=%s", self.name, uid, len(turns))
        if wait_msg is not None:
            try:
                await wait_msg.edit_text("⏳ Составляю молитву…")
            except Exception:
                pass

        prayer_text = await self._compose_prayer(uid, turns, force_variant="B")
        if not prayer_text:
            logger.warning("[%s] compose empty uid=%s", self.name, uid)
            return None, None

        logger.info(
            "[%s] compose done uid=%s chars=%s",
            self.name,
            uid,
            len(prayer_text),
        )

        if wait_msg is not None:
            try:
                await wait_msg.edit_text("⏳ Озвучиваю молитву…")
            except Exception:
                pass

        ogg = await self._synthesize_prayer_voice(
            uid, prayer_text, wait_msg=wait_msg
        )
        return prayer_text, ogg

    async def _synthesize_prayer_voice(
        self,
        uid: int,
        prayer_text: str,
        *,
        wait_msg: Optional[Message] = None,
        model_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        queue_label: Optional[str] = None,
        allow_fallback: bool = True,
    ) -> Optional[bytes]:
        """Озвучка молитвы через общую очередь (ElevenLabs → Voicebox/SpeechKit)."""
        # Спец-форматирование для TTS (паузы/SSML), без словаря ударений.
        tts_text = format_prayer_for_tts(prayer_text)
        if not tts_text.strip():
            logger.warning("[%s] prayer TTS empty after format uid=%s", self.name, uid)
            return None

        mid = (model_id or "").strip() or None
        vid = (voice_id or "").strip() or (self.elevenlabs_tts.voice_id or "").strip()
        logger.info(
            "[%s] prayer TTS input uid=%s chars=%s model=%s voice=%s preview=%r",
            self.name,
            uid,
            len(tts_text),
            mid or self.elevenlabs_tts.model_id,
            (vid[:8] + "…") if vid else "—",
            tts_text[:80],
        )

        async def _on_queued(ahead: int) -> None:
            await self._notify_tts_queue(wait_msg, ahead)

        async with self.tts_queue.hold(
            label=queue_label or f"prayer:{uid}",
            on_queued=_on_queued if wait_msg is not None else None,
        ):
            if wait_msg is not None:
                try:
                    await wait_msg.edit_text("⏳ Озвучиваю молитву…")
                except Exception:
                    pass

            if self.elevenlabs_tts.configured and vid:
                try:
                    tempo = resolve_prayer_tts_atempo()
                    raw_mp3 = await self.elevenlabs_tts.synthesize_ogg_opus(
                        tts_text,
                        voice_id=vid,
                        model_id=mid,
                        as_ogg=False,
                    )
                    mixed = await asyncio.to_thread(
                        mix_voice_with_bg_music,
                        raw_mp3,
                        atempo=tempo,
                        voice_suffix=".mp3",
                        bitrate="160k",
                    )
                    if mixed:
                        logger.info(
                            "[%s] prayer TTS ok engine=elevenlabs uid=%s model=%s voice=%s atempo=%.2f bytes=%s",
                            self.name,
                            uid,
                            mid or self.elevenlabs_tts.model_id,
                            vid[:8],
                            tempo,
                            len(mixed),
                        )
                        return mixed
                    from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus

                    ogg = await asyncio.to_thread(
                        audio_bytes_to_ogg_opus,
                        raw_mp3,
                        atempo=tempo,
                        prefix="elabs_prayer_",
                    )
                    if ogg:
                        logger.warning(
                            "[%s] prayer bg mix failed uid=%s — без фона",
                            self.name,
                            uid,
                        )
                        return ogg
                except Exception as e:
                    logger.error(
                        "[%s] prayer ElevenLabs failed uid=%s model=%s voice=%s: %s",
                        self.name,
                        uid,
                        mid or self.elevenlabs_tts.model_id,
                        vid[:8],
                        e,
                    )

            # Fallback на Voicebox/SpeechKit — только для основной озвучки юзеров.
            if not allow_fallback or mid or (
                voice_id
                and voice_id.strip()
                and voice_id.strip() != (self.elevenlabs_tts.voice_id or "").strip()
            ):
                logger.warning(
                    "[%s] prayer TTS unavailable uid=%s model=%s voice=%s",
                    self.name,
                    uid,
                    mid or self.elevenlabs_tts.model_id,
                    (vid[:8] if vid else "—"),
                )
                return None

            tts = self.tts
            if tts.configured:
                try:
                    if tts is self.voicebox:
                        ogg = await self.voicebox.synthesize_ogg_opus(tts_text)
                    else:
                        ogg = await tts.synthesize_ogg_opus(tts_text)
                    if ogg:
                        logger.info(
                            "[%s] prayer TTS ok engine=fallback uid=%s bytes=%s",
                            self.name,
                            uid,
                            len(ogg),
                        )
                        return ogg
                except Exception as e:
                    logger.error(
                        "[%s] prayer fallback TTS failed uid=%s: %s",
                        self.name,
                        uid,
                        e,
                    )

                if (
                    self.voicebox.configured
                    and self.speechkit.configured
                    and tts is self.voicebox
                ):
                    try:
                        ogg = await self.speechkit.synthesize_ogg_opus(tts_text)
                        if ogg:
                            return ogg
                    except Exception as e2:
                        logger.error(
                            "[%s] prayer SpeechKit fallback failed uid=%s: %s",
                            self.name,
                            uid,
                            e2,
                        )

        logger.warning("[%s] prayer TTS unavailable uid=%s", self.name, uid)
        return None

    async def _compose_and_synthesize_compare(
        self,
        uid: int,
        turns: List[str],
        *,
        wait_msg: Optional[Message] = None,
    ) -> tuple[Optional[dict[str, str]], dict[str, Optional[bytes]]]:
        """Админ-претест: тексты A+B × все TTS → до ~16 голосовых."""
        logger.info("[%s] compare compose start uid=%s (A+B)", self.name, uid)
        engines = self._compare_engine_specs()
        if wait_msg is not None:
            try:
                n_eng = len(engines)
                await wait_msg.edit_text(
                    f"⏳ Составляю варианты промпта A и B…\n"
                    f"Затем озвучу каждым из {n_eng} голосов "
                    f"(итого до {n_eng * 2} голосовых)."
                )
            except Exception:
                pass

        text_a, text_b = await asyncio.gather(
            self._compose_prayer(uid, turns, force_variant="A"),
            self._compose_prayer(uid, turns, force_variant="B"),
        )
        texts: dict[str, str] = {}
        if text_a:
            texts["A"] = text_a
        if text_b:
            texts["B"] = text_b
        if not texts:
            return None, {}

        if wait_msg is not None:
            try:
                listing = "\n".join(
                    f"• {v}: {spec[1]}" for v in texts for spec in engines
                )
                await wait_msg.edit_text(
                    f"⏳ Тексты готовы (A={'да' if 'A' in texts else 'нет'}, "
                    f"B={'да' if 'B' in texts else 'нет'}).\n"
                    f"Готовлю озвучку ({len(texts) * len(engines)} шт.):\n{listing}"
                )
            except Exception:
                pass

        async def _one(label: str, coro) -> tuple[str, Optional[bytes]]:
            try:
                audio = await coro
                logger.info(
                    "[%s] compare TTS ok uid=%s engine=%s bytes=%s",
                    self.name,
                    uid,
                    label,
                    len(audio) if audio else 0,
                )
                return label, audio
            except Exception as e:
                logger.error(
                    "[%s] compare TTS failed uid=%s engine=%s: %s",
                    self.name,
                    uid,
                    label,
                    e,
                )
                return label, None

        tasks = []
        out: dict[str, Optional[bytes]] = {}
        for variant, raw_text in texts.items():
            tts_text = format_prayer_for_tts(raw_text)
            for key, _title, client, voice_id in engines:
                label = f"{variant}:{key}"
                out[label] = None

                if key == "voicebox":

                    async def _voicebox(
                        text: str = tts_text, vb_label: str = label
                    ) -> Optional[bytes]:
                        if not self.voicebox.configured:
                            return None

                        async def _on_queued(ahead: int) -> None:
                            await self._notify_tts_queue(wait_msg, ahead)

                        async with self.tts_queue.hold(
                            label=f"compare-vb:{uid}:{vb_label}",
                            on_queued=_on_queued if wait_msg is not None else None,
                        ):
                            return await self.voicebox.synthesize_ogg_opus(text)

                    tasks.append(_one(label, _voicebox()))
                elif key.startswith("elevenlabs:"):
                    tasks.append(
                        _one(
                            label,
                            client.synthesize_ogg_opus(tts_text, voice_id=voice_id),
                        )
                    )
                else:
                    tasks.append(_one(label, client.synthesize_ogg_opus(tts_text)))

        if tasks:
            results = await asyncio.gather(*tasks)
            for label, audio in results:
                out[label] = audio
        return texts, out

    def _compare_engine_specs(
        self, *, log_missing: bool = True
    ) -> list[tuple[str, str, Any, Optional[str]]]:
        """(key, human title, client, voice_id) только для настроенных движков."""
        candidates: list[tuple[str, str, Any, Optional[str]]] = [
            (
                "openai",
                f"OpenAI ({self.openai_tts.model} / {self.openai_tts.voice})",
                self.openai_tts,
                None,
            ),
            (
                "yandex",
                f"Яндекс SpeechKit ({self.speechkit.voice})",
                self.speechkit,
                None,
            ),
            (
                "voicebox",
                "Voicebox (локальный / Константин)",
                self.voicebox,
                None,
            ),
            (
                "salute",
                f"SaluteSpeech ({self.salute_tts.voice})",
                self.salute_tts,
                None,
            ),
        ]
        ready: list[tuple[str, str, Any, Optional[str]]] = []
        for key, title, client, voice_id in candidates:
            if client.configured:
                ready.append((key, title, client, voice_id))
            elif log_missing:
                logger.warning("[%s] compare: %s не настроен — пропуск", self.name, title)

        if self.elevenlabs_tts.configured:
            voice_ids = self.elevenlabs_tts.compare_voice_ids_all
            for i, vid in enumerate(voice_ids, 1):
                ready.append(
                    (
                        f"elevenlabs:{vid}",
                        f"ElevenLabs #{i} ({vid[:8]}…)",
                        self.elevenlabs_tts,
                        vid,
                    )
                )
        elif log_missing:
            logger.warning("[%s] compare: ElevenLabs не настроен — пропуск", self.name)
        return ready

    async def _deliver_prayer_compare(
        self,
        message: Message,
        bot: Optional[Bot],
        prayer_texts: dict[str, str],
        voices: dict[str, Optional[bytes]],
    ) -> None:
        specs = self._compare_engine_specs(log_missing=False)
        variants = [v for v in ("A", "B") if (prayer_texts.get(v) or "").strip()]
        total = max(1, len(variants) * len(specs))
        chat_id = message.chat.id
        any_voice = False
        idx = 0

        for variant in variants:
            body = (prayer_texts.get(variant) or "").strip()
            header = (
                f"<b>🧪 Промпт {variant} — текст молитвы</b>\n\n"
                f"{html.escape(body)}"
            )
            if len(header) <= _TG_MESSAGE_MAX:
                await message.answer(header, parse_mode=ParseMode.HTML)
            else:
                first, rest = _split_caption(
                    body, _TG_MESSAGE_MAX - len(f"🧪 Промпт {variant}\n\n")
                )
                await message.answer(
                    f"<b>🧪 Промпт {variant} — текст молитвы</b>\n\n"
                    f"{html.escape(first)}",
                    parse_mode=ParseMode.HTML,
                )
                await _send_text_chunks(message, rest)

            for key, title, _client, _voice_id in specs:
                idx += 1
                caption = f"{idx}/{total} [{variant}] {title}"
                ogg = voices.get(f"{variant}:{key}")
                if not ogg:
                    await message.answer(
                        f"<i>{html.escape(caption)} — не удалось сгенерировать</i>",
                        parse_mode=ParseMode.HTML,
                    )
                    continue
                any_voice = True
                safe_name = f"{variant}_{key}".replace(":", "_")[:40]
                if bot:
                    await bot.send_voice(
                        chat_id,
                        BufferedInputFile(ogg, filename=f"prayer_{safe_name}.ogg"),
                        caption=caption[:1024],
                    )
                else:
                    await message.answer_voice(
                        BufferedInputFile(ogg, filename=f"prayer_{safe_name}.ogg"),
                        caption=caption[:1024],
                    )

        if not any_voice:
            await message.answer(
                "<i>Ни один TTS не вернул аудио.</i>",
                parse_mode=ParseMode.HTML,
            )

    async def _deliver_prayer(
        self,
        message: Message,
        bot: Optional[Bot],
        prayer_text: str,
        ogg: Optional[bytes],
    ) -> None:
        body = (prayer_text or "").strip()
        uid = message.from_user.id if message.from_user else 0
        intro_caption = _PRAYER_VOICE_CAPTION.strip()
        if len(intro_caption) > _TG_CAPTION_MAX:
            intro_caption = intro_caption[: _TG_CAPTION_MAX - 1].rstrip() + "…"

        if ogg:
            logger.info("[%s] sending prayer voice uid=%s bytes=%s", self.name, uid, len(ogg))
            try:
                # Аудио = молитва; подпись = короткая инструкция.
                voice_file = BufferedInputFile(ogg, filename="prayer.ogg")
                if bot:
                    kwargs = {
                        "chat_id": message.chat.id,
                        "voice": voice_file,
                        "caption": intro_caption,
                    }
                    if message.message_thread_id:
                        kwargs["message_thread_id"] = message.message_thread_id
                    await bot.send_voice(**kwargs)
                else:
                    await message.answer_voice(voice_file, caption=intro_caption)
                logger.info("[%s] prayer voice sent uid=%s", self.name, uid)
            except TelegramBadRequest as e:
                if not _is_voice_forbidden_error(e):
                    logger.error("[%s] prayer voice send failed uid=%s: %s", self.name, uid, e)
                    raise
                logger.info(
                    "[%s] voice forbidden for uid=%s — только текст",
                    self.name,
                    uid,
                )
                try:
                    await message.answer(
                        "<i>В этом чате голосовые недоступны — ниже текст молитвы.</i>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
        else:
            logger.info("[%s] prayer voice missing uid=%s — текст без аудио", self.name, uid)

        await self._deliver_prayer_text_with_donation(message, body)

    async def _deliver_admin_tts_compare(
        self,
        message: Message,
        bot: Optional[Bot],
        prayer_text: str,
    ) -> None:
        """
        Админам 4 варианта озвучки (тот же текст):
        1) prod voice + multilingual — уже ушёл в _deliver_prayer
        2) prod voice + flash
        3) compare voice (q5RNAd…) + multilingual
        4) compare voice + flash
        """
        if not (self.elevenlabs_tts.configured and self.elevenlabs_tts.voice_id):
            return

        primary_voice = (self.elevenlabs_tts.voice_id or "").strip()
        primary_model = (self.elevenlabs_tts.model_id or "eleven_multilingual_v2").strip()
        cheap_model = (
            getattr(self.config, "ELEVENLABS_CHEAP_MODEL_ID", None)
            or "eleven_flash_v2_5"
        ).strip() or "eleven_flash_v2_5"
        compare_voice = (
            getattr(self.config, "ELEVENLABS_ADMIN_COMPARE_VOICE_ID", None)
            or "q5RNAd4899271dg9W2K8"
        ).strip() or "q5RNAd4899271dg9W2K8"

        # (номер, caption, voice_id, model_id|None=primary)
        extras: list[tuple[int, str, str, Optional[str]]] = []
        if cheap_model != primary_model:
            extras.append(
                (
                    2,
                    f"2/4 голос prod ({primary_voice[:8]}…) + {cheap_model}",
                    primary_voice,
                    cheap_model,
                )
            )
        if compare_voice and compare_voice != primary_voice:
            extras.append(
                (
                    3,
                    f"3/4 голос {compare_voice[:8]}… + {primary_model}",
                    compare_voice,
                    None,
                )
            )
            if cheap_model != primary_model:
                extras.append(
                    (
                        4,
                        f"4/4 голос {compare_voice[:8]}… + {cheap_model}",
                        compare_voice,
                        cheap_model,
                    )
                )
        if not extras:
            return

        uid = message.from_user.id if message.from_user else 0
        try:
            await message.answer(
                "🧪 <b>Админ-сравнение TTS (4 варианта)</b>\n"
                f"1/4 уже выше: prod <code>{html.escape(primary_voice[:8])}…</code> "
                f"+ <code>{html.escape(primary_model)}</code>\n"
                f"Ниже ещё {len(extras)} варианта того же текста.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        for num, caption, vid, mid in extras:
            ogg = await self._synthesize_prayer_voice(
                uid,
                prayer_text,
                model_id=mid,
                voice_id=vid,
                queue_label=f"prayer-cmp{num}:{uid}",
                allow_fallback=False,
            )
            if not ogg:
                try:
                    await message.answer(
                        f"<i>{html.escape(caption)} — не удалось сгенерировать</i>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
                continue
            try:
                voice_file = BufferedInputFile(
                    ogg, filename=f"prayer_cmp_{num}.ogg"
                )
                safe_caption = caption[:1024]
                if bot:
                    kwargs = {
                        "chat_id": message.chat.id,
                        "voice": voice_file,
                        "caption": safe_caption,
                    }
                    if message.message_thread_id:
                        kwargs["message_thread_id"] = message.message_thread_id
                    await bot.send_voice(**kwargs)
                else:
                    await message.answer_voice(voice_file, caption=safe_caption)
                logger.info(
                    "[%s] admin compare #%s sent uid=%s voice=%s model=%s bytes=%s",
                    self.name,
                    num,
                    uid,
                    vid[:8],
                    mid or primary_model,
                    len(ogg),
                )
            except Exception as e:
                logger.error(
                    "[%s] admin compare #%s send failed uid=%s: %s",
                    self.name,
                    num,
                    uid,
                    e,
                )

    async def _deliver_prayer_text_only(
        self,
        message: Message,
        body: str,
        *,
        notice: Optional[str] = None,
    ) -> None:
        await self._deliver_prayer_text_with_donation(message, body)
        if notice:
            try:
                await message.answer(notice, parse_mode=ParseMode.HTML)
            except Exception:
                await message.answer(
                    notice.replace("<i>", "").replace("</i>", ""),
                )

    async def _deliver_prayer_text_with_donation(
        self, message: Message, body: str
    ) -> None:
        """
        1) Текст молитвы (plain, без HTML — надёжно доходит).
        2) Отдельным сообщением — донат-блок + кнопка.
        """
        body = (body or "").strip()
        uid = message.from_user.id if message.from_user else 0
        kb = self._prayer_support_kb()
        if uid:
            try:
                await self.user_storage.increment_donation_button_counter(uid)
            except Exception:
                pass

        # Молитва — обычный текст, без parse_mode (не ломается на символах LLM).
        prayer_msg = f"🙏 Ваша молитва\n\n{body}" if body else "🙏 Ваша молитва"
        try:
            if len(prayer_msg) <= _TG_MESSAGE_MAX:
                await message.answer(prayer_msg)
            else:
                first, rest = _split_caption(
                    body, _TG_MESSAGE_MAX - len("🙏 Ваша молитва\n\n")
                )
                await message.answer(f"🙏 Ваша молитва\n\n{first}")
                await _send_text_chunks(message, rest)
            logger.info(
                "[%s] prayer text sent uid=%s chars=%s",
                self.name,
                uid,
                len(body),
            )
        except Exception as e:
            logger.error(
                "[%s] prayer text send failed uid=%s: %s",
                self.name,
                uid,
                e,
                exc_info=True,
            )
            # Последняя попытка — кусками без заголовка.
            try:
                await _send_text_chunks(message, body)
            except Exception as e2:
                logger.error("[%s] prayer text chunks failed uid=%s: %s", self.name, uid, e2)

        try:
            await message.answer(
                _PRAYER_DONATION_FOOTER_HTML,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            logger.info("[%s] donation footer sent uid=%s", self.name, uid)
        except TelegramBadRequest as e:
            logger.warning(
                "[%s] donation HTML failed uid=%s: %s — plain fallback",
                self.name,
                uid,
                e,
            )
            plain = (
                "Генерация голоса требует много ресурсов и стоит довольно дорого, "
                "поэтому каждое ваше пожертвование помогает нам сохранять и развивать "
                "эту функцию.\n\n"
                "«Носите бремена друг друга, и таким образом исполните закон Христов.»\n"
                "Гал. 6:2"
            )
            await message.answer(plain, reply_markup=kb)

    def _prayer_support_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Поддержать проект",
                        callback_data="payment_start",
                    )
                ]
            ]
        )

    async def _compose_prayer(
        self,
        user_id: int,
        turns: List[str],
        *,
        force_variant: Optional[str] = None,
    ) -> Optional[str]:
        if not self.agents_client:
            return None
        if force_variant in {"A", "B", "a", "b"}:
            variant = force_variant.upper()  # type: ignore[assignment]
        else:
            variant = pick_prayer_compose_variant(user_id)
        system_prompt = resolve_prayer_compose_system_prompt(variant)
        turns_block = _format_user_context(turns)
        style_examples = ""
        if variant == "B":
            try:
                style_examples = await fetch_prayer_style_examples(turns_block)
            except Exception as e:
                logger.warning("[%s] prayer RAG examples failed: %s", self.name, e)
        user_content = build_compose_user_content(
            turns_block, style_examples=style_examples
        )
        max_tokens = prayer_compose_max_tokens(variant)
        request_kind = prayer_compose_request_kind(variant)
        logger.info(
            "[%s] compose variant=%s uid=%s rag_chars=%s max_tokens=%s",
            self.name,
            variant,
            user_id,
            len(style_examples or ""),
            max_tokens,
        )
        raw = await self.agents_client.complete(
            system_prompt=system_prompt,
            user_content=user_content,
            user_id=user_id,
            request_kind=request_kind,
            temperature=0.55,
            max_tokens=max_tokens,
        )
        if not raw:
            return None
        return _strip_prayer_text(raw)

    async def on_prayer_stress_open(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        # Поправка ударений отключена.
        await callback.answer("Эта функция временно отключена.", show_alert=True)

    async def _handle_stress_feedback(
        self, message: Message, state: FSMContext, content: str
    ) -> None:
        max_words = int(self.config.PRAYER_STRESS_MAX_WORDS_PER_SUBMIT or 20)
        words, bad = parse_prayer_stress_words(content, limit=max_words)
        if not words:
            await message.answer(
                "Не увидел корректных слов.\n"
                "Нужно прислать через запятую и сделать ударную гласную большой.\n"
                "Пример: <code>амИнь, мОре</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        contexts = await self._store_and_send_stress_proposals(message, words)
        await state.clear()
        note = ""
        if bad:
            note = (
                "\n\nНе распознал: "
                + ", ".join(html.escape(x) for x in bad[:10])
            )
        await message.answer(
            "Спасибо! После модерации мы добавим эти слова в словарь ударений."
            + note,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Поддержать проект",
                            callback_data="payment_start",
                        )
                    ]
                ]
            ),
        )
        logger.info(
            "[%s] prayer stress feedback uid=%s accepted=%s rejected=%s stored=%s",
            self.name,
            message.from_user.id if message.from_user else 0,
            len(words),
            len(bad),
            len(contexts),
        )

    async def _store_and_send_stress_proposals(
        self, message: Message, words: List[PrayerStressWord]
    ) -> List[_PrayerStressProposalContext]:
        out: List[_PrayerStressProposalContext] = []
        uid = message.from_user.id if message.from_user else 0
        username = message.from_user.username if message.from_user else ""
        first_name = message.from_user.first_name if message.from_user else ""
        chat_id = int(message.chat.id)
        for word in words:
            sample_text = build_prayer_stress_sample_text(word.accented_word)
            row_id = await self.user_storage.create_prayer_stress_proposal(
                user_id=uid,
                username=username or "",
                first_name=first_name or "",
                chat_id=chat_id,
                source_word=word.source_word,
                base_word=word.base_word,
                accented_word=word.accented_word,
                sample_text=sample_text,
            )
            if not row_id:
                continue
            ctx = _PrayerStressProposalContext(
                proposal_id=int(row_id),
                source_word=word.source_word,
                base_word=word.base_word,
                accented_word=word.accented_word,
                sample_text=sample_text,
            )
            out.append(ctx)
            await self._send_stress_proposal_to_admin(message, ctx)
        return out

    async def _send_stress_proposal_to_admin(
        self, message: Message, ctx: _PrayerStressProposalContext
    ) -> None:
        if not self.bot:
            return
        admin_cid = admin_channel_chat_id()
        if admin_cid is None:
            logger.warning("[%s] prayer stress: ADMIN_CHANNEL_ID is empty", self.name)
            return

        reply_markup = self._prayer_stress_moderation_kb(ctx.proposal_id)
        username = message.from_user.username if message.from_user else ""
        username_line = f"@{html.escape(username)}" if username else "—"
        first_name = message.from_user.first_name if message.from_user else ""
        caption = (
            "<b>Ударение для словаря молитв</b>\n"
            f"Слово: <code>{html.escape(ctx.accented_word)}</code>\n"
            f"Пользователь: id=<code>{message.from_user.id if message.from_user else 0}</code>\n"
            f"Ник: {username_line}\n"
            f"Имя: {html.escape(first_name or '')}\n\n"
            f"Добавить в словарь: <code>{html.escape(ctx.accented_word)}</code>"
        )
        kwargs: dict[str, Any] = {
            "chat_id": admin_cid,
            "caption": caption,
            "parse_mode": ParseMode.HTML,
            "reply_markup": reply_markup,
        }
        tid = int(self.config.PRAYER_STRESS_MODERATION_THREAD_ID or 0)
        if tid > 0:
            kwargs["message_thread_id"] = tid
        reply_to_mid = int(self.config.PRAYER_STRESS_MODERATION_REPLY_TO_MESSAGE_ID or 0)
        if reply_to_mid > 0:
            kwargs["reply_to_message_id"] = reply_to_mid

        voice_msg_id: Optional[int] = None
        tts = self.tts
        if tts.configured:
            try:
                if tts is self.voicebox:
                    async with self.tts_queue.hold(
                        label=f"stress:{ctx.proposal_id}"
                    ):
                        ogg = await self.voicebox.synthesize_ogg_opus(ctx.sample_text)
                else:
                    ogg = await tts.synthesize_ogg_opus(ctx.sample_text)
                msg = await self.bot.send_voice(
                    voice=BufferedInputFile(ogg, filename=f"stress_{ctx.proposal_id}.ogg"),
                    **kwargs,
                )
                voice_msg_id = int(msg.message_id)
            except Exception as e:
                logger.error("[%s] prayer stress voice preview failed: %s", self.name, e)
                if (
                    tts is self.voicebox
                    and self.speechkit.configured
                    and voice_msg_id is None
                ):
                    try:
                        ogg = await self.speechkit.synthesize_ogg_opus(ctx.sample_text)
                        msg = await self.bot.send_voice(
                            voice=BufferedInputFile(
                                ogg, filename=f"stress_{ctx.proposal_id}.ogg"
                            ),
                            **kwargs,
                        )
                        voice_msg_id = int(msg.message_id)
                        logger.info(
                            "[%s] prayer stress preview SpeechKit fallback ok id=%s",
                            self.name,
                            ctx.proposal_id,
                        )
                    except Exception as e2:
                        logger.error(
                            "[%s] prayer stress SpeechKit preview failed: %s",
                            self.name,
                            e2,
                        )

        text_msg_id: Optional[int] = None
        if voice_msg_id is None:
            text = caption + f"\n\nТестовая фраза: <code>{html.escape(ctx.sample_text)}</code>"
            msg_kwargs = {k: v for k, v in kwargs.items() if k != "caption"}
            msg = await self.bot.send_message(text=text, **msg_kwargs)
            text_msg_id = int(msg.message_id)

        if voice_msg_id or text_msg_id:
            await self.user_storage.set_prayer_stress_proposal_admin_message_ids(
                ctx.proposal_id,
                admin_chat_id=int(admin_cid) if isinstance(admin_cid, int) else 0,
                admin_voice_message_id=voice_msg_id,
                admin_text_message_id=text_msg_id,
            )

    async def on_prayer_stress_moderation(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 3:
            await callback.answer("bad callback")
            return
        _, action, proposal_s = parts
        uid = callback.from_user.id if callback.from_user else 0
        if not uid or not await is_admin_or_super(self.user_storage, uid):
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        try:
            proposal_id = int(proposal_s)
        except ValueError:
            await callback.answer("bad proposal")
            return
        proposal = await self.user_storage.get_prayer_stress_proposal(proposal_id)
        if not proposal:
            await callback.answer("Не найдено", show_alert=True)
            return
        if proposal.get("status") != "pending":
            await callback.answer("Уже обработано")
            try:
                if callback.message:
                    await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if action == "yes":
            await self.user_storage.upsert_prayer_stress_dictionary_word(
                base_word=str(proposal["base_word"]),
                accented_word=str(proposal["accented_word"]),
                source_word=str(proposal["source_word"]),
                approved_by=uid,
            )
            await self.user_storage.decide_prayer_stress_proposal(
                proposal_id, status="approved", decided_by=uid
            )
            self._stress_dict_cache[str(proposal["base_word"])] = str(proposal["accented_word"])
            await callback.answer("Добавлено в словарь")
        else:
            await self.user_storage.decide_prayer_stress_proposal(
                proposal_id, status="rejected", decided_by=uid
            )
            await callback.answer("Отклонено")
        try:
            if callback.message:
                await callback.message.delete()
        except Exception:
            try:
                if callback.message:
                    await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

    async def _apply_prayer_stress_dictionary(self, prayer_text: str) -> str:
        # Словарь ударений отключён: текст пользователю и TTS без подмен.
        return prayer_text

    def _prayer_stress_feedback_kb(self) -> InlineKeyboardMarkup:
        # Оставлено для совместимости; в доставке молитвы больше не используется.
        return self._prayer_support_kb()

    def _prayer_stress_moderation_kb(self, proposal_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да",
                        callback_data=f"{_PRAYER_STRESS_DECIDE_PREFIX}yes:{proposal_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Нет",
                        callback_data=f"{_PRAYER_STRESS_DECIDE_PREFIX}no:{proposal_id}",
                    ),
                ]
            ]
        )


def _split_caption(text: str, limit: int) -> tuple[str, str]:
    """Разделить текст на caption (≤ limit) и хвост."""
    t = text or ""
    if len(t) <= limit:
        return t, ""
    window = t[:limit]
    cut = window.rfind("\n\n")
    if cut < limit // 3:
        cut = window.rfind("\n")
    if cut < limit // 3:
        cut = window.rfind(" ")
    if cut < limit // 3:
        cut = limit
    return t[:cut].rstrip(), t[cut:].lstrip()


async def _send_text_chunks(message: Message, text: str) -> None:
    rest = (text or "").strip()
    while rest:
        chunk, rest = _split_caption(rest, _TG_MESSAGE_MAX)
        if not chunk:
            break
        await message.answer(chunk)
