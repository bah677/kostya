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
from bot.services.prayer_tts_style import ogg_opus_duration_sec, resolve_prayer_tts_atempo
from bot.services.tg_voice_delivery import (
    is_voice_forbidden_error,
    prepare_ogg_bytes,
    send_tg_audio_payload,
    TgAudioKind,
)
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
    PRAYER_COMPOSE_MAX_ATTEMPTS,
    PRAYER_INTAKE_SYSTEM_PROMPT,
    pick_prayer_compose_variant,
    prayer_compose_max_tokens,
    prayer_compose_request_kind,
    prayer_text_looks_complete,
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

_PRAYER_DONATION_FOOTER = (
    "Друзья, голосовая молитва требует времени и ресурсов, но мы с радостью "
    "вкладываем их в эту функцию, потому что знаем, как она важна для вас. "
    "Если вы хотите, чтобы она оставалась доступной, поддержите нас "
    "пожертвованием — любая сумма станет вкладом в общее дело любви.\n\n"
    "«Носите бремена друг друга, и таким образом исполните закон Христов» "
    "(Гал. 6:2)"
)

_PRAYER_VOICE_UNLOCK_CB = "payment_start_prayer_voice"
_PRAYER_VOICE_SUPPORT_CB = "payment_start"

# Фрагмент молитвы для сравнения голосов (админы / разовая рассылка).
_PRAYER_VOICE_SAMPLE_TEXT = (
    "Отец Небесный, благодарю Тебя за этот день. "
    "Укрепи моё сердце, направь мысли и дай мир там, где тревожно. "
    "Пусть Твоё слово будет светом на моём пути. Аминь."
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
    return is_voice_forbidden_error(exc)


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
        self._bot_app: Optional[Any] = None
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
        self._funding = None

    @property
    def tts(self) -> _TTS:
        """Primary non-ElevenLabs engine. SpeechKit Zakhar — только failover, не primary."""
        return self.voicebox

    def set_bot(self, app) -> None:
        self._bot_app = app
        self.bot = app.bot if app is not None else None
        self._funding = None

    def _voice_funding(self):
        if self._funding is None:
            from bot.services.prayer_voice_funding import PrayerVoiceFundingService

            conv = getattr(self._bot_app, "currency_converter", None) if self._bot_app else None
            self._funding = PrayerVoiceFundingService(self.user_storage, conv)
        return self._funding

    async def _log_tech_incident(
        self, uid: int, kind: str, *, detail: str = ""
    ) -> None:
        if uid <= 0:
            return
        try:
            await self.user_storage.log_prayer_tech_incident(
                uid, kind, detail=detail
            )
        except Exception as e:
            logger.debug("[%s] log tech incident uid=%s: %s", self.name, uid, e)

    async def initialize(self) -> None:
        self.agents_client = AgentsClient(self.user_storage)
        await self.user_storage.ensure_prayer_stress_schema()
        try:
            await self.user_storage.ensure_prayer_voice_quota_schema()
        except Exception as e:
            logger.warning("[%s] prayer voice quota schema: %s", self.name, e)
        try:
            await self.user_storage.ensure_prayer_tech_incidents_schema()
        except Exception as e:
            logger.warning("[%s] prayer tech incidents schema: %s", self.name, e)
        self._stress_dict_cache = await self.user_storage.get_prayer_stress_dictionary()
        if self.voicebox.configured:
            logger.info(
                "[%s] Voicebox TTS готов (profile=%s atempo=%s queue_max=%s)",
                self.name,
                self.voicebox.profile_id[:8],
                self.voicebox.atempo,
                self.tts_queue.max_concurrent,
            )
        else:
            logger.warning(
                "[%s] Voicebox не настроен — primary без локального синтеза",
                self.name,
            )
        if self.speechkit.configured:
            logger.info(
                "[%s] SpeechKit Zakhar готов как failover (voice=%s)",
                self.name,
                self.speechkit.voice,
            )
        if not self.voicebox.configured and not self.speechkit.configured:
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
        await self._start_prayer(
            message, state, args=(command.args or "").strip(), edit=False
        )

    async def on_prayer_callback(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        """Кнопка рассылки: callback_data=prayer_start → как /prayer."""
        await callback.answer()
        if not callback.message:
            return
        await self._start_prayer(callback.message, state, args="", edit=False)

    async def start_from_menu(
        self, message: Message, state: FSMContext, *, edit: bool = False
    ) -> None:
        await self._start_prayer(message, state, args="", edit=edit)

    async def _start_prayer(
        self,
        message: Message,
        state: FSMContext,
        *,
        args: str = "",
        edit: bool = False,
    ) -> None:
        from bot.utils.user_ui import render_user_screen

        await state.clear()
        await state.set_state(PrayerStates.collecting)
        await state.update_data(prayer_turns=[], clarify_count=0)

        if args:
            await self._on_user_turn(message, state, args)
            return

        await render_user_screen(
            message,
            text=(
                "Напишите одной строкой, о чём на сердце.\n\n"
                "Например: «тревога за сына» или «нет сил и страшно».\n\n"
                "Я помолюсь об этом с вами."
            ),
            edit=edit,
            parse_mode=ParseMode.HTML,
            add_main_menu=True,
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
        voice_access: Optional[dict] = None
        voice_unlocked_by_admin = False
        voice_allowed = True

        try:
            voice_unlocked_by_admin = bool(
                uid and await is_admin_or_super(self.user_storage, uid)
            )
            if uid and not voice_unlocked_by_admin:
                voice_access = await self._voice_funding().try_acquire_access(uid)
                voice_allowed = voice_access is not None
                if not voice_allowed:
                    try:
                        await wait_msg.edit_text("⏳ Составляю молитву…")
                    except Exception:
                        pass

            if bot:
                async with record_voice_chat_action(
                    bot, message.chat.id, message_thread_id=message.message_thread_id
                ):
                    prayer_text, ogg = await self._compose_and_synthesize(
                        uid,
                        turns,
                        wait_msg=wait_msg,
                        skip_voice=not voice_allowed,
                    )
            else:
                prayer_text, ogg = await self._compose_and_synthesize(
                    uid,
                    turns,
                    wait_msg=wait_msg,
                    skip_voice=not voice_allowed,
                )

            if not prayer_text:
                if voice_access:
                    await self.user_storage.release_prayer_voice_access(
                        voice_access, uid
                    )
                    voice_access = None
                await self._log_tech_incident(uid, "compose_failed")
                await wait_msg.edit_text(
                    "Не удалось составить молитву. Попробуйте позже или /prayer снова."
                )
                return

            if uid:
                try:
                    await self.user_storage.save_prayer_last_text(uid, prayer_text)
                except Exception as e:
                    logger.warning("[%s] save last prayer text uid=%s: %s", self.name, uid, e)

            if voice_allowed and not ogg and voice_access:
                await self._log_tech_incident(uid, "voice_tts_failed")
                # TTS не удался — возвращаем слот/бонус
                await self.user_storage.release_prayer_voice_access(voice_access, uid)
                voice_access = None
            elif voice_allowed and not ogg:
                await self._log_tech_incident(uid, "voice_tts_failed")

            try:
                await wait_msg.delete()
            except Exception:
                pass

            if voice_allowed:
                await self._deliver_prayer(
                    message,
                    bot,
                    prayer_text,
                    ogg,
                    include_donation_footer=True,
                    pool_progress=not voice_unlocked_by_admin,
                )
            else:
                await self._deliver_prayer_text_only(message, prayer_text)
                await self._send_voice_limit_notice(message)

            logger.info(
                "[%s] prayer delivered uid=%s voice=%s access=%s admin=%s",
                self.name,
                uid,
                bool(ogg),
                (voice_access or {}).get("source") if voice_access else None,
                voice_unlocked_by_admin,
            )
        except Exception as e:
            if voice_access:
                await self.user_storage.release_prayer_voice_access(voice_access, uid)
            if message.from_user:
                await self._log_tech_incident(
                    message.from_user.id, "generate_failed", detail=str(e)[:500]
                )
            logger.error("[%s] generate failed uid=%s: %s", self.name, uid, e, exc_info=True)
            try:
                await wait_msg.edit_text(
                    "Не удалось подготовить молитву. Попробуйте позже или /prayer снова."
                )
            except Exception:
                pass
        finally:
            await state.clear()

    async def _send_voice_limit_notice(self, message: Message) -> None:
        next_slots = 0
        try:
            next_slots = await self._voice_funding().indicative_next_slots()
        except Exception as e:
            logger.debug("[%s] next_slots for limit notice: %s", self.name, e)
        text = (
            "Лимит бесплатных голосовых молитв на сегодня закончился.\n\n"
            "Если хотите голос сейчас — поддержите проект любым донатом, "
            "и мы сразу озвучим эту молитву.\n"
            "Или попробуйте завтра с <b>08:00 мск</b> попасть в бесплатный лимит.\n\n"
            "Каждый донат учитывается в лимите на завтра — сейчас уже собрано "
            f"на <b>{next_slots}</b> бесплатных молитв."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Поддержать проект",
                        callback_data=_PRAYER_VOICE_UNLOCK_CB,
                    )
                ]
            ]
        )
        try:
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            logger.error("[%s] voice limit notice failed: %s", self.name, e)

    async def _format_pool_progress_html(self) -> str:
        status = await self._voice_funding().get_status()
        limit = int(status.get("limit") or 0)
        used = int(status.get("used") or 0)
        remaining = int(status.get("remaining") or 0)
        next_slots = int(status.get("next_slots") or 0)
        return (
            f"Вчера сообщество поддержало проект — этого хватило на "
            f"<b>{limit}</b> бесплатных голосовых молитв.\n"
            f"Уже израсходовано: <b>{used}</b>. Осталось: <b>{remaining}</b>.\n\n"
            "Можете поддержать проект и внести вклад в завтрашний лимит — "
            f"сейчас на завтра уже собрано на <b>{next_slots}</b> бесплатных молитв."
        )

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

    async def _synthesize_elevenlabs_prayer(
        self,
        uid: int,
        tts_text: str,
        *,
        voice_id: str,
    ) -> Optional[bytes]:
        vid = (voice_id or "").strip()
        if not vid or not self.elevenlabs_tts.configured:
            return None
        tempo = resolve_prayer_tts_atempo()
        raw_mp3 = await self.elevenlabs_tts.synthesize_ogg_opus(
            tts_text,
            voice_id=vid,
            as_ogg=False,
        )
        mixed = await asyncio.to_thread(
            mix_voice_with_bg_music,
            raw_mp3,
            atempo=tempo,
            voice_suffix=".mp3",
        )
        if mixed:
            logger.info(
                "[%s] prayer TTS ok engine=elevenlabs uid=%s voice=%s atempo=%.2f bytes=%s",
                self.name,
                uid,
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
            logger.warning("[%s] prayer bg mix failed uid=%s voice=%s", self.name, uid, vid[:8])
        return ogg

    async def _compose_and_synthesize(
        self,
        uid: int,
        turns: List[str],
        *,
        wait_msg: Optional[Message] = None,
        skip_voice: bool = False,
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

        if skip_voice:
            logger.info("[%s] skip TTS (quota) uid=%s", self.name, uid)
            return prayer_text, None

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
        voice_id: Optional[str] = None,
        elevenlabs_only: bool = False,
        wait_msg: Optional[Message] = None,
        queue_label: Optional[str] = None,
    ) -> Optional[bytes]:
        """Озвучка молитвы через общую очередь (ElevenLabs → Voicebox/SpeechKit)."""
        tts_text = format_prayer_for_tts(prayer_text)
        if not tts_text.strip():
            logger.warning("[%s] prayer TTS empty after format uid=%s", self.name, uid)
            return None

        vid = (voice_id or self.elevenlabs_tts.voice_id or "").strip()
        logger.info(
            "[%s] prayer TTS input uid=%s chars=%s model=%s voice=%s preview=%r",
            self.name,
            uid,
            len(tts_text),
            self.elevenlabs_tts.model_id,
            (vid[:8] + "…") if vid else "—",
            tts_text[:80],
        )

        async def _on_queued(ahead: int) -> None:
            await self._notify_tts_queue(wait_msg, ahead)

        hold_label = queue_label or f"prayer:{uid}"
        async with self.tts_queue.hold(
            label=hold_label,
            on_queued=_on_queued if wait_msg is not None else None,
        ):
            if wait_msg is not None:
                try:
                    await wait_msg.edit_text("⏳ Озвучиваю молитву…")
                except Exception:
                    pass

            # 1) ElevenLabs — основной, если настроен
            if self.elevenlabs_tts.configured and vid:
                try:
                    ogg = await self._synthesize_elevenlabs_prayer(
                        uid, tts_text, voice_id=vid
                    )
                    if ogg:
                        return ogg
                    logger.warning(
                        "[%s] prayer ElevenLabs empty uid=%s → next engine",
                        self.name,
                        uid,
                    )
                except Exception as e:
                    logger.error(
                        "[%s] prayer ElevenLabs failed uid=%s voice=%s: %s",
                        self.name,
                        uid,
                        vid[:8],
                        e,
                    )

            if elevenlabs_only:
                return None

            # 2) Voicebox (локальный) — второй основной, если настроен
            if self.voicebox.configured:
                try:
                    ogg = await self.voicebox.synthesize_ogg_opus(tts_text)
                    if ogg:
                        logger.info(
                            "[%s] prayer TTS ok engine=voicebox uid=%s bytes=%s",
                            self.name,
                            uid,
                            len(ogg),
                        )
                        return ogg
                    logger.warning(
                        "[%s] prayer Voicebox empty uid=%s → SpeechKit failover",
                        self.name,
                        uid,
                    )
                except Exception as e:
                    logger.error(
                        "[%s] prayer Voicebox failed uid=%s: %s → SpeechKit failover",
                        self.name,
                        uid,
                        e,
                    )

            # 3) Yandex Zakhar — только failover после отказа primary, не «вместо»
            if self.speechkit.configured:
                try:
                    ogg = await self.speechkit.synthesize_ogg_opus(tts_text)
                    if ogg:
                        logger.info(
                            "[%s] prayer TTS ok engine=speechkit_failover uid=%s bytes=%s",
                            self.name,
                            uid,
                            len(ogg),
                        )
                        return ogg
                except Exception as e2:
                    logger.error(
                        "[%s] prayer SpeechKit failover failed uid=%s: %s",
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
                duration = ogg_opus_duration_sec(ogg)
                voice_file = BufferedInputFile(ogg, filename=f"prayer_{safe_name}.ogg")
                send_kwargs: dict[str, Any] = {"caption": caption[:1024]}
                if duration is not None:
                    send_kwargs["duration"] = duration
                if bot:
                    await bot.send_voice(chat_id, voice_file, **send_kwargs)
                else:
                    await message.answer_voice(voice_file, **send_kwargs)

        if not any_voice:
            await message.answer(
                "<i>Ни один TTS не вернул аудио.</i>",
                parse_mode=ParseMode.HTML,
            )

    async def _send_prayer_audio(
        self,
        *,
        chat_id: int,
        ogg: bytes,
        caption: str,
        bot: Optional[Bot] = None,
        message: Optional[Message] = None,
        message_thread_id: Optional[int] = None,
        filename_base: str = "prayer",
        uid: int = 0,
    ) -> bool:
        payload = prepare_ogg_bytes(ogg, filename_base=filename_base)
        if not payload:
            return False
        logger.info(
            "[%s] sending prayer %s uid=%s bytes=%s duration=%s",
            self.name,
            payload.kind.value,
            uid,
            len(payload.data),
            payload.duration_sec,
        )
        try:
            await send_tg_audio_payload(
                chat_id,
                payload,
                bot=bot,
                message=message,
                caption=caption,
                message_thread_id=message_thread_id,
            )
            return True
        except TelegramBadRequest as e:
            if payload.kind == TgAudioKind.VOICE and _is_voice_forbidden_error(e):
                logger.info(
                    "[%s] voice forbidden uid=%s — только текст",
                    self.name,
                    uid,
                )
                if message:
                    try:
                        await message.answer(
                            "<i>В этом чате голосовые недоступны — ниже текст молитвы.</i>",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass
                return False
            raise

    async def _deliver_prayer(
        self,
        message: Message,
        bot: Optional[Bot],
        prayer_text: str,
        ogg: Optional[bytes],
        *,
        include_donation_footer: bool = True,
        pool_progress: bool = False,
    ) -> None:
        body = (prayer_text or "").strip()
        uid = message.from_user.id if message.from_user else 0
        intro_caption = _PRAYER_VOICE_CAPTION.strip()
        if len(intro_caption) > _TG_CAPTION_MAX:
            intro_caption = intro_caption[: _TG_CAPTION_MAX - 1].rstrip() + "…"

        if ogg:
            sent = await self._send_prayer_audio(
                chat_id=message.chat.id,
                ogg=ogg,
                caption=intro_caption,
                bot=bot,
                message=message if not bot else None,
                message_thread_id=message.message_thread_id,
                uid=uid,
            )
            if sent:
                logger.info("[%s] prayer audio sent uid=%s", self.name, uid)
                if uid:
                    try:
                        await self.user_storage.log_interaction(
                            user_id=uid,
                            event_category="prayer",
                            event_type="prayer_voice_sent",
                            data={},
                        )
                    except Exception:
                        pass
                    try:
                        # первая молитва → nudge через 4 дня
                        n = await self.user_storage.count_sent_prayer_voices(uid)
                        if n <= 1:
                            snippet = ""
                            # topic из последнего user в истории / caption
                            hist = await self.user_storage.get_private_chat_history(
                                uid, limit=12
                            )
                            for row in hist:
                                if row.get("role") == "user":
                                    from storage.db.second_prayer_nudge import (
                                        clip_topic_snippet,
                                    )

                                    snippet = clip_topic_snippet(str(row.get("content") or ""))
                                    break
                            await self.user_storage.schedule_second_prayer_nudge(
                                uid, snippet, delay_days=4
                            )
                    except Exception as e:
                        logger.debug("[%s] schedule nudge uid=%s: %s", self.name, uid, e)
            else:
                ogg = None
                if uid:
                    await self._log_tech_incident(uid, "voice_send_failed")
        else:
            logger.info("[%s] prayer voice missing uid=%s — текст без аудио", self.name, uid)

        await self._deliver_prayer_text_with_donation(
            message,
            body,
            include_donation_footer=include_donation_footer,
            pool_progress=pool_progress,
        )

    async def _deliver_prayer_text_only(
        self,
        message: Message,
        body: str,
        *,
        notice: Optional[str] = None,
    ) -> None:
        await self._deliver_prayer_text_with_donation(
            message, body, include_donation_footer=False
        )
        if notice:
            try:
                await message.answer(notice, parse_mode=ParseMode.HTML)
            except Exception:
                await message.answer(
                    notice.replace("<i>", "").replace("</i>", ""),
                )

    async def _deliver_prayer_text_with_donation(
        self,
        message: Message,
        body: str,
        *,
        include_donation_footer: bool = True,
        pool_progress: bool = False,
    ) -> None:
        """
        1) Текст молитвы (plain, без HTML — надёжно доходит).
        2) Отдельным сообщением — пул лимита / донат-блок + кнопка.
        """
        body = (body or "").strip()
        uid = message.from_user.id if message.from_user else 0
        kb = self._prayer_support_kb()

        if uid and include_donation_footer:
            try:
                from bot.services.crisis_classifier import is_crisis_context
                from openai_client.agents_client import AgentsClient

                inbound = (message.text or message.caption or "").strip()
                if not inbound:
                    try:
                        hist = await self.user_storage.get_private_chat_history(
                            uid, limit=6
                        )
                        for row in reversed(hist):
                            if row.get("role") == "user" and (row.get("content") or "").strip():
                                inbound = str(row["content"]).strip()
                                break
                    except Exception:
                        pass
                agents = AgentsClient(self.user_storage)
                if await is_crisis_context(
                    self.user_storage,
                    agents,
                    uid,
                    inbound,
                    point="prayer",
                ):
                    include_donation_footer = False
                    await self.user_storage.set_owed_donation_ask(uid, True)
            except Exception as e:
                logger.warning("[%s] crisis check prayer uid=%s: %s", self.name, uid, e)
                include_donation_footer = False
                try:
                    await self.user_storage.set_owed_donation_ask(uid, True)
                except Exception:
                    pass

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
            try:
                await _send_text_chunks(message, body)
            except Exception as e2:
                logger.error("[%s] prayer text chunks failed uid=%s: %s", self.name, uid, e2)

        if not include_donation_footer:
            return

        footer = _PRAYER_DONATION_FOOTER
        parse_mode = None
        if pool_progress:
            try:
                footer = await self._format_pool_progress_html()
                parse_mode = ParseMode.HTML
            except Exception as e:
                logger.warning("[%s] pool progress footer failed: %s", self.name, e)

        try:
            await message.answer(footer, reply_markup=kb, parse_mode=parse_mode)
            if uid:
                try:
                    await self.user_storage.increment_donation_button_counter(uid)
                except Exception:
                    pass
            logger.info("[%s] donation footer sent uid=%s pool=%s", self.name, uid, pool_progress)
        except Exception as e:
            logger.error(
                "[%s] donation footer failed uid=%s: %s",
                self.name,
                uid,
                e,
            )

    async def deliver_unlock_voice_for_user(
        self,
        user_id: int,
        prayer_text: str,
    ) -> bool:
        """После доната-разблокировки: голос + текст, без донат-футера."""
        body = (prayer_text or "").strip()
        if not body or not self.bot or user_id <= 0:
            return False
        try:
            ogg = await self._synthesize_prayer_voice(user_id, body)
        except Exception as e:
            logger.error(
                "[%s] unlock TTS failed uid=%s: %s",
                self.name,
                user_id,
                e,
                exc_info=True,
            )
            ogg = None

        intro_caption = _PRAYER_VOICE_CAPTION.strip()
        if len(intro_caption) > _TG_CAPTION_MAX:
            intro_caption = intro_caption[: _TG_CAPTION_MAX - 1].rstrip() + "…"

        if ogg:
            try:
                sent = await self._send_prayer_audio(
                    chat_id=user_id,
                    ogg=ogg,
                    caption=intro_caption,
                    bot=self.bot,
                    filename_base="prayer",
                    uid=user_id,
                )
                if not sent:
                    ogg = None
            except Exception as e:
                logger.error(
                    "[%s] unlock audio send failed uid=%s: %s",
                    self.name,
                    user_id,
                    e,
                    exc_info=True,
                )
                ogg = None

        prayer_msg = f"🙏 Ваша молитва\n\n{body}"
        try:
            if len(prayer_msg) <= _TG_MESSAGE_MAX:
                await self.bot.send_message(user_id, prayer_msg)
            else:
                first, rest = _split_caption(
                    body, _TG_MESSAGE_MAX - len("🙏 Ваша молитва\n\n")
                )
                await self.bot.send_message(user_id, f"🙏 Ваша молитва\n\n{first}")
                # остаток — одним куском / несколькими
                chunk = rest
                while chunk:
                    part = chunk[:_TG_MESSAGE_MAX]
                    chunk = chunk[_TG_MESSAGE_MAX:]
                    await self.bot.send_message(user_id, part)
        except Exception as e:
            logger.error(
                "[%s] unlock text send failed uid=%s: %s",
                self.name,
                user_id,
                e,
                exc_info=True,
            )
            return False

        if not ogg:
            try:
                await self.bot.send_message(
                    user_id,
                    "Не удалось сразу озвучить молитву. Мы уже сохранили ваш донат — "
                    "попробуйте /prayer чуть позже или напишите в поддержку.",
                )
            except Exception:
                pass
            return False
        return True

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
        # DeepSeek V4: thinking+content делят max_tokens. При обрыве (нет амИнь /
        # finish=length) — retry с ужатым thinking, не больше 3 попыток.
        retry_nudge = (
            "\n\nВАЖНО: предыдущий ответ оборвался на середине. "
            "Напиши молитву ПОЛНОСТЬЮ от начала до финала ровно строкой "
            "«Во имя Иисуса Христа, амИнь». Без длинных рассуждений — сразу текст молитвы."
        )
        last_text: Optional[str] = None
        for attempt in range(1, PRAYER_COMPOSE_MAX_ATTEMPTS + 1):
            if attempt >= PRAYER_COMPOSE_MAX_ATTEMPTS:
                thinking = "disabled"
                effort = None
            else:
                thinking = "enabled"
                effort = "low"
            prompt_user = user_content if attempt == 1 else (user_content + retry_nudge)
            result = await self.agents_client.complete_ex(
                system_prompt=system_prompt,
                user_content=prompt_user,
                user_id=user_id,
                request_kind=request_kind,
                temperature=0.55,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=effort,
            )
            text = _strip_prayer_text(result.text or "")
            if text:
                last_text = text
            truncated = (result.finish_reason or "").lower() == "length"
            looks_ok = bool(text) and prayer_text_looks_complete(text) and not truncated
            if looks_ok:
                if attempt > 1:
                    logger.info(
                        "[%s] compose ok after retry uid=%s attempt=%s chars=%s",
                        self.name,
                        user_id,
                        attempt,
                        len(text),
                    )
                return text
            logger.warning(
                "[%s] compose incomplete uid=%s attempt=%s/%s finish=%s "
                "chars=%s reasoning_tokens=%s completion_tokens=%s tail=%r",
                self.name,
                user_id,
                attempt,
                PRAYER_COMPOSE_MAX_ATTEMPTS,
                result.finish_reason,
                len(text or ""),
                result.reasoning_tokens,
                result.completion_tokens,
                (text or "")[-80:],
            )
            if attempt < PRAYER_COMPOSE_MAX_ATTEMPTS:
                await asyncio.sleep(0.4 * attempt)
        logger.error(
            "[%s] compose still incomplete after %s attempts uid=%s chars=%s",
            self.name,
            PRAYER_COMPOSE_MAX_ATTEMPTS,
            user_id,
            len(last_text or ""),
        )
        return last_text

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
        # Primary: Voicebox; SpeechKit Zakhar — только после отказа primary
        if self.voicebox.configured:
            try:
                async with self.tts_queue.hold(
                    label=f"stress:{ctx.proposal_id}"
                ):
                    ogg = await self.voicebox.synthesize_ogg_opus(ctx.sample_text)
                stress_kwargs = dict(kwargs)
                dur = ogg_opus_duration_sec(ogg)
                if dur is not None:
                    stress_kwargs["duration"] = dur
                msg = await self.bot.send_voice(
                    voice=BufferedInputFile(ogg, filename=f"stress_{ctx.proposal_id}.ogg"),
                    **stress_kwargs,
                )
                voice_msg_id = int(msg.message_id)
            except Exception as e:
                logger.error("[%s] prayer stress voice preview failed: %s", self.name, e)

        if voice_msg_id is None and self.speechkit.configured:
            try:
                ogg = await self.speechkit.synthesize_ogg_opus(ctx.sample_text)
                stress_kwargs = dict(kwargs)
                dur = ogg_opus_duration_sec(ogg)
                if dur is not None:
                    stress_kwargs["duration"] = dur
                msg = await self.bot.send_voice(
                    voice=BufferedInputFile(
                        ogg, filename=f"stress_{ctx.proposal_id}.ogg"
                    ),
                    **stress_kwargs,
                )
                voice_msg_id = int(msg.message_id)
                logger.info(
                    "[%s] prayer stress preview SpeechKit failover ok id=%s",
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
