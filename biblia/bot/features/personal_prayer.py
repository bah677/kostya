"""Команда /prayer: свободный рассказ → до 2 уточнений → молитва + голос."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, List, Optional, Protocol

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message

from bot.features.base import BaseFeature
from bot.services.voicebox_tts import VoiceboxPrayerTTS, format_prayer_for_tts
from bot.services.yandex_speechkit import YandexSpeechKitTTS
from bot.states import PrayerStates
from bot.utils.chat_actions import record_voice_chat_action
from openai_client.agents_client import AgentsClient
from openai_client.prayer_prompt import (
    PRAYER_COMPOSE_SYSTEM_PROMPT,
    PRAYER_INTAKE_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

_MAX_CLARIFY = 2
_TG_CAPTION_MAX = 1024
_TG_MESSAGE_MAX = 4096
_CANCEL_WORDS = frozenset(
    {"отмена", "отменить", "стоп", "cancel", "/cancel"}
)


class _TTS(Protocol):
    @property
    def configured(self) -> bool: ...

    async def synthesize_ogg_opus(self, text: str) -> bytes: ...


def _strip_prayer_text(raw: str) -> str:
    return format_prayer_for_tts(raw)


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

    async def on_prayer_command(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        await state.clear()
        args = (command.args or "").strip()
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
                    prayer_text, ogg = await self._compose_and_synthesize(uid, turns)
            else:
                prayer_text, ogg = await self._compose_and_synthesize(uid, turns)

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
        turns: List[str],
    ) -> tuple[Optional[str], Optional[bytes]]:
        logger.info("[%s] compose start uid=%s turns=%s", self.name, uid, len(turns))
        prayer_text = await self._compose_prayer(uid, turns)
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
                if tts is self.voicebox and self.speechkit.configured:
                    logger.info("[%s] TTS fallback SpeechKit uid=%s", self.name, uid)
                    try:
                        ogg = await self.speechkit.synthesize_ogg_opus(prayer_text)
                    except Exception as e2:
                        logger.error(
                            "[%s] SpeechKit fallback failed uid=%s: %s",
                            self.name,
                            uid,
                            e2,
                        )
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
        body = (prayer_text or "").strip()
        if ogg and bot:
            logger.info(
                "[%s] sending voice uid=%s",
                self.name,
                message.from_user.id if message.from_user else 0,
            )
            caption, rest = _split_caption(body, _TG_CAPTION_MAX)
            await bot.send_voice(
                message.chat.id,
                BufferedInputFile(ogg, filename="prayer.ogg"),
                caption=caption or None,
            )
            await _send_text_chunks(message, rest)
            return

        header = "<b>🙏 Ваша молитва</b>\n\n"
        safe = html.escape(body)
        # Заголовок + текст; при переполнении — остаток обычными сообщениями.
        full = header + safe
        if len(full) <= _TG_MESSAGE_MAX:
            await message.answer(full, parse_mode=ParseMode.HTML)
        else:
            # Первый кусок без HTML-разрыва посередине тега: шлём plain.
            first, rest = _split_caption(body, _TG_MESSAGE_MAX - len("🙏 Ваша молитва\n\n"))
            await message.answer(
                f"<b>🙏 Ваша молитва</b>\n\n{html.escape(first)}",
                parse_mode=ParseMode.HTML,
            )
            await _send_text_chunks(message, rest)

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

    async def _compose_prayer(self, user_id: int, turns: List[str]) -> Optional[str]:
        if not self.agents_client:
            return None
        raw = await self.agents_client.complete(
            system_prompt=PRAYER_COMPOSE_SYSTEM_PROMPT,
            user_content=_format_user_context(turns),
            user_id=user_id,
            request_kind="personal_prayer_compose",
            temperature=0.55,
            max_tokens=1200,
        )
        if not raw:
            return None
        return _strip_prayer_text(raw)


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
