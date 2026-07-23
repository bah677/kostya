"""Команда /prayer: анкета → молитва (DeepSeek) → голосовое (Voicebox / SpeechKit)."""

from __future__ import annotations

import html
import logging
from typing import List, Optional, Protocol

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message

from bot.features.base import BaseFeature
from bot.services.voicebox_tts import VoiceboxPrayerTTS, format_prayer_for_tts
from bot.services.yandex_speechkit import YandexSpeechKitTTS
from bot.states import PrayerStates
from bot.utils.chat_actions import record_voice_chat_action
from openai_client.agents_client import AgentsClient
from openai_client.prayer_prompt import PRAYER_COMPOSE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_PRAYER_QUESTIONS: tuple[str, ...] = (
    "Что сейчас больше всего на душе? Опишите коротко, своими словами.",
    "Какая молитва вам нужна: о помощи и утешении, благодарности, покаянии, "
    "мире в семье — или о чём-то другом?",
    "К кому обратиться: к Господу Иисусу Христу, к Богу Отцу, "
    "или как вам привычно? (можно ответить «как получится»)",
)

_CANCEL_WORDS = frozenset(
    {"отмена", "отменить", "стоп", "cancel", "/cancel"}
)


class _TTS(Protocol):
    @property
    def configured(self) -> bool: ...

    async def synthesize_ogg_opus(self, text: str) -> bytes: ...


def _strip_prayer_text(raw: str) -> str:
    """Очистить ответ LLM и нормализовать паузы для озвучки."""
    return format_prayer_for_tts(raw)


class PersonalPrayerFeature(BaseFeature):
    name = "personal_prayer"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot: Optional[Bot] = None
        self.agents_client: Optional[AgentsClient] = None
        self.voicebox = VoiceboxPrayerTTS()
        self.speechkit = YandexSpeechKitTTS()

    @property
    def tts(self) -> _TTS:
        if self.voicebox.configured:
            return self.voicebox
        return self.speechkit

    def set_bot(self, app) -> None:
        self.bot = app.bot if app is not None else None

    async def initialize(self) -> None:
        self.agents_client = AgentsClient(self.user_storage)
        if self.voicebox.configured:
            logger.info(
                "[%s] Voicebox TTS готов (profile=%s atempo=%s)",
                self.name,
                self.voicebox.profile_id[:8],
                self.voicebox.atempo,
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

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.on_prayer_command, Command(commands=["prayer", "molitva"]))
        logger.info("[%s] Команды /prayer /molitva", self.name)

    async def on_prayer_command(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self._begin_interview(message, state)

    async def _begin_interview(self, message: Message, state: FSMContext) -> None:
        await state.set_state(PrayerStates.collecting)
        await state.update_data(prayer_answers=[])

        await message.answer(
            "<b>🙏 Персональная молитва</b>\n\n"
            "Я задам три коротких вопроса, затем составлю спокойную молитву "
            "на современном языке — и озвучу её голосом.\n\n"
            f"<b>1.</b> {_PRAYER_QUESTIONS[0]}\n\n"
            "<i>Чтобы прервать — напишите «отмена» или снова /prayer</i>",
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
            await message.answer("Напишите ответ текстом или «отмена».")
            return

        data = await state.get_data()
        answers: List[str] = list(data.get("prayer_answers") or [])
        answers.append(content)
        await state.update_data(prayer_answers=answers)

        if len(answers) < len(_PRAYER_QUESTIONS):
            n = len(answers) + 1
            await message.answer(
                f"<b>{n}.</b> {_PRAYER_QUESTIONS[len(answers)]}",
                parse_mode=ParseMode.HTML,
            )
            return

        await self._generate_and_send(message, state, answers)

    async def _generate_and_send(
        self,
        message: Message,
        state: FSMContext,
        answers: List[str],
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
                    prayer_text, ogg = await self._compose_and_synthesize(uid, answers)
            else:
                prayer_text, ogg = await self._compose_and_synthesize(uid, answers)

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
            logger.info("[%s] prayer delivered uid=%s voice=%s", self.name, uid, bool(ogg))
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

    async def _compose_and_synthesize(
        self,
        uid: int,
        answers: List[str],
    ) -> tuple[Optional[str], Optional[bytes]]:
        logger.info("[%s] compose start uid=%s", self.name, uid)
        prayer_text = await self._compose_prayer(uid, answers)
        if not prayer_text:
            logger.warning("[%s] compose empty uid=%s", self.name, uid)
            return None, None

        logger.info(
            "[%s] compose done uid=%s chars=%s",
            self.name,
            uid,
            len(prayer_text),
        )

        ogg: Optional[bytes] = None
        tts = self.tts
        if tts.configured:
            engine = "voicebox" if tts is self.voicebox else "speechkit"
            logger.info("[%s] TTS start uid=%s engine=%s", self.name, uid, engine)
            try:
                ogg = await tts.synthesize_ogg_opus(prayer_text)
            except Exception as e:
                logger.error("[%s] TTS failed uid=%s engine=%s: %s", self.name, uid, engine, e)
                # Fallback: Voicebox упал → попробовать SpeechKit, если он есть.
                if tts is self.voicebox and self.speechkit.configured:
                    logger.info("[%s] TTS fallback SpeechKit uid=%s", self.name, uid)
                    try:
                        ogg = await self.speechkit.synthesize_ogg_opus(prayer_text)
                    except Exception as e2:
                        logger.error("[%s] SpeechKit fallback failed uid=%s: %s", self.name, uid, e2)
            else:
                logger.info(
                    "[%s] TTS done uid=%s bytes=%s",
                    self.name,
                    uid,
                    len(ogg) if ogg else 0,
                )
        else:
            logger.warning("[%s] TTS skipped — not configured uid=%s", self.name, uid)

        return prayer_text, ogg

    async def _deliver_prayer(
        self,
        message: Message,
        bot: Optional[Bot],
        prayer_text: str,
        ogg: Optional[bytes],
    ) -> None:
        safe = html.escape(prayer_text)
        if ogg and bot:
            logger.info("[%s] sending voice uid=%s", self.name, message.from_user.id if message.from_user else 0)
            await bot.send_voice(
                message.chat.id,
                BufferedInputFile(ogg, filename="prayer.ogg"),
            )
            return

        await message.answer(
            f"<b>🙏 Ваша молитва</b>\n\n{safe}",
            parse_mode=ParseMode.HTML,
        )
        if not self.tts.configured:
            await message.answer(
                "<i>Голосовое временно недоступно (не настроен TTS).</i>",
                parse_mode=ParseMode.HTML,
            )
        elif not ogg:
            await message.answer(
                "<i>Не удалось озвучить молитву — отправляю текстом.</i>",
                parse_mode=ParseMode.HTML,
            )

    async def _compose_prayer(self, user_id: int, answers: List[str]) -> Optional[str]:
        if not self.agents_client:
            return None

        lines = []
        for i, (q, a) in enumerate(zip(_PRAYER_QUESTIONS, answers), 1):
            lines.append(f"Вопрос {i}: {q}\nОтвет: {a}")
        user_block = "\n\n".join(lines)

        raw = await self.agents_client.complete(
            system_prompt=PRAYER_COMPOSE_SYSTEM_PROMPT,
            user_content=user_block,
            user_id=user_id,
            request_kind="personal_prayer_compose",
            temperature=0.55,
            max_tokens=1200,
        )
        if not raw:
            return None
        return _strip_prayer_text(raw)
