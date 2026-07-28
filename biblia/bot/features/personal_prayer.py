"""Команда /prayer: свободный рассказ → до 2 уточнений → молитва + голос."""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
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
from bot.services.prayer_stress import (
    PrayerStressWord,
    apply_prayer_stress_dictionary,
    build_prayer_stress_sample_text,
    dictionary_hits,
    parse_prayer_stress_words,
)
from bot.services.voicebox_tts import VoiceboxPrayerTTS, format_prayer_for_tts
from bot.services.yandex_speechkit import YandexSpeechKitTTS
from bot.states import PrayerStates
from bot.utils.admin_channel import admin_channel_chat_id
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
_PRAYER_STRESS_OPEN_CB = "prayer_stress_open"
_PRAYER_STRESS_DECIDE_PREFIX = "pst:"


@dataclass(frozen=True)
class _PrayerStressProposalContext:
    proposal_id: int
    source_word: str
    base_word: str
    accented_word: str
    sample_text: str


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
            await self._handle_stress_feedback(message, state, content)
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

        tts_text = await self._apply_prayer_stress_dictionary(prayer_text)

        ogg: Optional[bytes] = None
        tts = self.tts
        if tts.configured:
            engine = "voicebox" if tts is self.voicebox else "speechkit"
            logger.info("[%s] TTS start uid=%s engine=%s", self.name, uid, engine)
            try:
                ogg = await tts.synthesize_ogg_opus(tts_text)
            except Exception as e:
                logger.error("[%s] TTS failed uid=%s engine=%s: %s", self.name, uid, engine, e)
                if tts is self.voicebox and self.speechkit.configured:
                    logger.info("[%s] TTS fallback SpeechKit uid=%s", self.name, uid)
                    try:
                        ogg = await self.speechkit.synthesize_ogg_opus(tts_text)
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
        else:
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

        if self.config.PRAYER_STRESS_FEEDBACK_ENABLED:
            await message.answer(
                "Генерация голоса требует много ресурсов и стоит довольно дорого, "
                "поэтому каждое ваше пожертвование помогает нам сохранять и развивать "
                "эту функцию.\n\n"
                "<blockquote>Носите бремена друг друга, и таким образом исполните закон Христов.</blockquote>\n"
                "<i>Гал. 6:2</i>\n\n"
                "Если услышали ошибку в ударении, вы тоже можете нам помочь — "
                "просто нажмите кнопку внизу.",
                parse_mode=ParseMode.HTML,
                reply_markup=self._prayer_stress_feedback_kb(),
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

    async def on_prayer_stress_open(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        await callback.answer()
        await state.set_state(PrayerStates.waiting_stress_feedback)
        await state.update_data(prayer_stress_origin="prayer")
        if callback.message:
            await callback.message.answer(
                "Пришлите слова через запятую и выделите ударную гласную большой буквой.\n\n"
                "Например: <code>амИнь, мОре</code>",
                parse_mode=ParseMode.HTML,
            )

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
                ogg = await tts.synthesize_ogg_opus(ctx.sample_text)
                msg = await self.bot.send_voice(
                    voice=BufferedInputFile(ogg, filename=f"stress_{ctx.proposal_id}.ogg"),
                    **kwargs,
                )
                voice_msg_id = int(msg.message_id)
            except Exception as e:
                logger.error("[%s] prayer stress voice preview failed: %s", self.name, e)

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
        if not self.config.PRAYER_STRESS_FEEDBACK_ENABLED:
            return prayer_text
        if not self._stress_dict_cache:
            self._stress_dict_cache = await self.user_storage.get_prayer_stress_dictionary()
        hits = dictionary_hits(prayer_text, self._stress_dict_cache)
        if hits:
            logger.info("[%s] prayer stress applied words=%s", self.name, hits)
        return apply_prayer_stress_dictionary(prayer_text, self._stress_dict_cache)

    def _prayer_stress_feedback_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Поправить ударение",
                        callback_data=_PRAYER_STRESS_OPEN_CB,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Поддержать проект",
                        callback_data="payment_start",
                    )
                ],
            ]
        )

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
