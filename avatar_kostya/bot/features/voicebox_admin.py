"""Админ-команды Voicebox: загрузка sample и тест голоса в личке."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.filters.private_only import PRIVATE_CHAT, CALLBACK_PRIVATE_CHAT
from bot.services.voicebox_client import VoiceboxClient, VoiceboxError
from config import config

logger = logging.getLogger(__name__)

CB_PICK = "vb:pick:"
CB_ADD = "vb:add:"
CB_ADD_MORE = "vb:more:"
CB_CANCEL = "vb:cancel"
CB_TX_OK = "vb:tx:ok"
CB_TX_EDIT = "vb:tx:edit"

# Лимит Voicebox validate_reference_audio
_SAMPLE_MAX_SEC = 30.0
_SAMPLE_MIN_SEC = 2.0


class VoiceSampleStates(StatesGroup):
    waiting_name = State()
    waiting_audio = State()
    waiting_transcript_confirm = State()
    waiting_transcript_edit = State()


class VoiceTestStates(StatesGroup):
    waiting_profile = State()
    waiting_text = State()


class VoiceboxAdminFeature(BaseFeature):
    """Минимальный UI Voicebox в личке аватара (только админы)."""

    name = "voicebox_admin"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._client = VoiceboxClient()

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def _enabled(self) -> bool:
        return bool(getattr(config, "VOICEBOX_ENABLED", False)) and self._client.configured

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(
            self.cmd_voice_sample, PRIVATE_CHAT, Command("voice_sample")
        )
        dispatcher.message.register(
            self.cmd_voice_add_sample, PRIVATE_CHAT, Command("voice_add_sample")
        )
        dispatcher.message.register(
            self.cmd_voice_test, PRIVATE_CHAT, Command("voice_test")
        )
        dispatcher.message.register(
            self.cmd_voice_models, PRIVATE_CHAT, Command("voice_models")
        )
        dispatcher.message.register(
            self.cmd_cancel,
            PRIVATE_CHAT,
            Command("cancel"),
            StateFilter(
                VoiceSampleStates.waiting_name,
                VoiceSampleStates.waiting_audio,
                VoiceSampleStates.waiting_transcript_confirm,
                VoiceSampleStates.waiting_transcript_edit,
                VoiceTestStates.waiting_profile,
                VoiceTestStates.waiting_text,
            ),
        )
        dispatcher.message.register(
            self.on_sample_name,
            PRIVATE_CHAT,
            StateFilter(VoiceSampleStates.waiting_name),
        )
        dispatcher.message.register(
            self.on_sample_audio,
            PRIVATE_CHAT,
            StateFilter(VoiceSampleStates.waiting_audio),
        )
        dispatcher.message.register(
            self.on_transcript_edit_text,
            PRIVATE_CHAT,
            StateFilter(VoiceSampleStates.waiting_transcript_edit),
        )
        dispatcher.message.register(
            self.on_transcript_confirm_text,
            PRIVATE_CHAT,
            StateFilter(VoiceSampleStates.waiting_transcript_confirm),
        )
        dispatcher.message.register(
            self.on_test_text,
            PRIVATE_CHAT,
            StateFilter(VoiceTestStates.waiting_text),
        )
        dispatcher.callback_query.register(
            self.on_pick_profile,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith(CB_PICK),
        )
        dispatcher.callback_query.register(
            self.on_pick_add_profile,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith(CB_ADD),
        )
        dispatcher.callback_query.register(
            self.on_add_more,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith(CB_ADD_MORE),
        )
        dispatcher.callback_query.register(
            self.on_tx_ok,
            CALLBACK_PRIVATE_CHAT,
            F.data == CB_TX_OK,
        )
        dispatcher.callback_query.register(
            self.on_tx_edit,
            CALLBACK_PRIVATE_CHAT,
            F.data == CB_TX_EDIT,
        )
        dispatcher.callback_query.register(
            self.on_cancel_cb,
            CALLBACK_PRIVATE_CHAT,
            F.data == CB_CANCEL,
        )

    async def _guard(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return False
        if not self._enabled():
            await message.answer(
                "Voicebox выключен. Задайте <code>VOICEBOX_ENABLED=1</code> "
                "и <code>VOICEBOX_BASE_URL</code> в .env.",
                parse_mode=ParseMode.HTML,
            )
            return False
        return True

    async def _clear_sample_tmp(self, state: FSMContext) -> None:
        data = await state.get_data()
        audio_dir = data.get("audio_dir")
        if audio_dir:
            shutil.rmtree(audio_dir, ignore_errors=True)

    async def cmd_cancel(self, message: Message, state: FSMContext) -> None:
        await self._clear_sample_tmp(state)
        await state.clear()
        await message.answer("Отменено.")

    async def on_cancel_cb(self, callback: CallbackQuery, state: FSMContext) -> None:
        await self._clear_sample_tmp(state)
        await state.clear()
        await callback.answer("Отменено")
        if callback.message:
            await callback.message.answer("Отменено.")

    async def cmd_voice_models(self, message: Message, state: FSMContext) -> None:
        if not await self._guard(message):
            return
        await state.clear()
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            logger.exception("voice_models: %s", e)
            await message.answer(f"Не удалось получить список моделей: {e}")
            return
        if not profiles:
            await message.answer("Моделей пока нет. Создайте через /voice_sample")
            return
        default_id = (config.VOICEBOX_DEFAULT_PROFILE_ID or "").strip()
        lines = ["<b>Модели Voicebox</b>\n"]
        for p in profiles:
            mark = " ★" if p.get("id") == default_id else ""
            lines.append(
                f"• <b>{_esc(p.get('name') or '?')}</b>{mark}\n"
                f"  <code>{p.get('id')}</code> · samples={p.get('sample_count', 0)} · "
                f"engine={p.get('default_engine') or '—'}"
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _find_profile_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """Точное совпадение имени, как в Voicebox API."""
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            logger.exception("list profiles for name check: %s", e)
            raise
        for p in profiles:
            if (p.get("name") or "") == name:
                return p
        return None

    async def _accept_profile_name(
        self, message: Message, state: FSMContext, name: str
    ) -> bool:
        """Проверить имя и перейти к ожиданию sample. False = имя отклонено."""
        name = name.strip()
        if not name or name.startswith("/"):
            await message.answer("Нужно текстовое имя модели (не команда).")
            return False
        if len(name) > 100:
            await message.answer("Имя слишком длинное (макс. 100).")
            return False
        try:
            existing = await self._find_profile_by_name(name)
        except Exception as e:
            await message.answer(f"Не удалось проверить имена моделей: {e}")
            return False
        if existing:
            await message.answer(
                f"Имя <b>{_esc(name)}</b> уже занято "
                f"(<code>{existing.get('id')}</code>).\n"
                "Введите другое имя. Список: /voice_models",
                parse_mode=ParseMode.HTML,
            )
            return False
        await state.update_data(profile_name=name, sample_mode="create")
        await state.set_state(VoiceSampleStates.waiting_audio)
        await message.answer(
            f"Модель: <b>{_esc(name)}</b>\n\n"
            f"Пришлите sample (голос/аудио). Обрежем до {int(_SAMPLE_MAX_SEC)} сек.",
            parse_mode=ParseMode.HTML,
        )
        return True

    async def cmd_voice_add_sample(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        """Добавить sample к уже существующей модели."""
        if not await self._guard(message):
            return
        await self._clear_sample_tmp(state)
        await state.clear()
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            logger.exception("voice_add_sample list: %s", e)
            await message.answer(f"Не удалось получить модели: {e}")
            return
        if not profiles:
            await message.answer("Моделей нет. Сначала /voice_sample")
            return

        arg = (command.args or "").strip()
        if arg:
            by_id = next((p for p in profiles if p.get("id") == arg), None)
            by_name = next((p for p in profiles if (p.get("name") or "") == arg), None)
            chosen = by_id or by_name
            if not chosen:
                await message.answer(
                    "Модель не найдена. Список: /voice_models\n"
                    "Или /voice_add_sample без аргументов — выбрать кнопкой."
                )
                return
            await self._start_add_sample_for_profile(
                message, state, chosen["id"], chosen.get("name") or chosen["id"]
            )
            return

        if len(profiles) == 1:
            p = profiles[0]
            await self._start_add_sample_for_profile(
                message, state, p["id"], p.get("name") or p["id"]
            )
            return

        rows = []
        for p in profiles[:20]:
            label = f"{p.get('name') or '?'} ({p.get('sample_count', 0)} smp)"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label[:64],
                        callback_data=f"{CB_ADD}{p['id']}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)])
        await message.answer(
            "Выберите модель, в которую добавить sample:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _start_add_sample_for_profile(
        self,
        message: Message,
        state: FSMContext,
        profile_id: str,
        profile_name: str,
    ) -> None:
        await state.update_data(
            sample_mode="add",
            profile_id=profile_id,
            profile_name=profile_name,
        )
        await state.set_state(VoiceSampleStates.waiting_audio)
        await message.answer(
            f"Добавление sample в <b>{_esc(profile_name)}</b>\n"
            f"<code>{profile_id}</code>\n\n"
            f"Пришлите голосовое/аудио (обрежем до {int(_SAMPLE_MAX_SEC)} сек).\n"
            "Можно повторить 2–3 раза через /voice_add_sample.\n"
            "/cancel — отмена",
            parse_mode=ParseMode.HTML,
        )

    async def on_pick_add_profile(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только админ", show_alert=True)
            return
        if not self._enabled():
            await callback.answer("Voicebox выключен", show_alert=True)
            return
        pid = (callback.data or "")[len(CB_ADD) :]
        if not pid:
            await callback.answer("Нет id", show_alert=True)
            return
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            await callback.answer(str(e)[:180], show_alert=True)
            return
        profile = next((p for p in profiles if p.get("id") == pid), None)
        if not profile:
            await callback.answer("Модель не найдена", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await self._start_add_sample_for_profile(
                callback.message,
                state,
                pid,
                profile.get("name") or pid,
            )

    async def on_add_more(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только админ", show_alert=True)
            return
        pid = (callback.data or "")[len(CB_ADD_MORE) :]
        if not pid:
            await callback.answer("Нет id", show_alert=True)
            return
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            await callback.answer(str(e)[:180], show_alert=True)
            return
        profile = next((p for p in profiles if p.get("id") == pid), None)
        if not profile:
            await callback.answer("Модель не найдена", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await self._start_add_sample_for_profile(
                callback.message,
                state,
                pid,
                profile.get("name") or pid,
            )

    async def cmd_voice_sample(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        if not await self._guard(message):
            return
        await self._clear_sample_tmp(state)
        await state.clear()
        name = (command.args or "").strip()
        if name:
            ok = await self._accept_profile_name(message, state, name)
            if not ok:
                await state.set_state(VoiceSampleStates.waiting_name)
            return
        await state.set_state(VoiceSampleStates.waiting_name)
        await message.answer(
            "Создание модели голоса.\n\n"
            "1) Напишите <b>имя модели</b> (уникальное)\n"
            "2) Пришлите sample — обрежем до 30 сек и пришлём превью\n"
            "3) Подтвердите или поправьте транскрипцию\n\n"
            "/cancel — отмена · /voice_models — занятые имена",
            parse_mode=ParseMode.HTML,
        )

    async def on_sample_name(self, message: Message, state: FSMContext) -> None:
        await self._accept_profile_name(message, state, message.text or "")

    async def on_sample_audio(self, message: Message, state: FSMContext) -> None:
        file_id, filename = _extract_audio(message)
        if not file_id:
            await message.answer(
                "Нужно голосовое, аудио или документ с аудио. /cancel — отмена."
            )
            return
        bot = message.bot
        tmp_dir = Path(tempfile.mkdtemp(prefix="vb_sample_"))
        raw_path = tmp_dir / filename
        status = await message.answer("Скачиваю и обрезаю sample…")
        try:
            tg_file = await bot.get_file(file_id)
            await bot.download_file(tg_file.file_path, destination=raw_path)
        except Exception as e:
            logger.exception("voice_sample download: %s", e)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await status.edit_text(f"Не удалось скачать файл: {e}")
            return
        if not raw_path.is_file() or raw_path.stat().st_size < 1000:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await status.edit_text("Файл слишком маленький.")
            return

        clip_path = tmp_dir / "sample_clip.ogg"
        try:
            duration, trimmed = await asyncio.to_thread(
                _trim_to_ogg, raw_path, clip_path, _SAMPLE_MAX_SEC
            )
        except Exception as e:
            logger.exception("voice_sample trim: %s", e)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await status.edit_text(f"Не удалось обработать аудио: {e}")
            return

        if duration < _SAMPLE_MIN_SEC:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await status.edit_text(
                f"Слишком коротко ({duration:.1f} с). Нужно минимум "
                f"{_SAMPLE_MIN_SEC:.0f} с."
            )
            return

        prev = await state.get_data()
        old_dir = prev.get("audio_dir")
        if old_dir:
            shutil.rmtree(old_dir, ignore_errors=True)

        # Превью того, что реально уйдёт в Voicebox
        try:
            await message.answer_voice(
                BufferedInputFile(clip_path.read_bytes(), filename="sample.ogg"),
                caption=(
                    f"Sample для загрузки: <b>{duration:.1f} с</b>"
                    + (" (обрезано до 30 с)" if trimmed else "")
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.exception("voice_sample preview: %s", e)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await status.edit_text(f"Не удалось отправить превью: {e}")
            return

        await status.edit_text("Распознаю текст (Whisper)…")
        uid = message.from_user.id if message.from_user else 0
        transcript = await self._transcribe(clip_path, uid, duration)
        transcript = _clean_transcript(transcript)

        await state.update_data(
            audio_path=str(clip_path),
            audio_dir=str(tmp_dir),
            audio_filename="sample_clip.ogg",
            duration_sec=duration,
            transcript=transcript or "",
        )
        await state.set_state(VoiceSampleStates.waiting_transcript_confirm)

        if transcript:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Верно", callback_data=CB_TX_OK
                        ),
                        InlineKeyboardButton(
                            text="✏️ Исправить", callback_data=CB_TX_EDIT
                        ),
                    ],
                    [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)],
                ]
            )
            await status.edit_text(
                "Распознанный текст sample:\n\n"
                f"<i>{_esc(transcript)}</i>\n\n"
                "Правильно поняли? Если нет — нажмите «Исправить» или просто "
                "пришлите верный текст сообщением.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await state.set_state(VoiceSampleStates.waiting_transcript_edit)
            await status.edit_text(
                "Речь не распозналась. Пришлите <b>точный текст</b> sample "
                "сообщением.\n/cancel — отмена",
                parse_mode=ParseMode.HTML,
            )

    async def _transcribe(
        self, audio_path: Path, user_id: int, duration: float
    ) -> Optional[str]:
        openai_client = getattr(self._app, "openai_client", None) if self._app else None
        if openai_client is None:
            logger.error("voice_sample: openai_client недоступен")
            return None
        try:
            return await openai_client.transcribe_voice(
                audio_file_path=str(audio_path),
                user_id=user_id,
                duration_sec=int(duration),
            )
        except Exception as e:
            logger.exception("voice_sample whisper: %s", e)
            return None

    async def on_tx_ok(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только админ", show_alert=True)
            return
        data = await state.get_data()
        text = (data.get("transcript") or "").strip()
        if not text:
            await callback.answer("Нет текста — исправьте вручную", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await self._finalize_sample(callback.message, state, text)

    async def on_tx_edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только админ", show_alert=True)
            return
        await state.set_state(VoiceSampleStates.waiting_transcript_edit)
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(
                "Пришлите <b>исправленный текст</b> sample одним сообщением.",
                parse_mode=ParseMode.HTML,
            )

    async def on_transcript_confirm_text(
        self, message: Message, state: FSMContext
    ) -> None:
        """В состоянии confirm текст = ручная правка."""
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            await message.answer(
                "Пришлите текст sample или нажмите «Верно» / «Исправить». "
                "/cancel — отмена."
            )
            return
        await self._finalize_sample(message, state, text)

    async def on_transcript_edit_text(
        self, message: Message, state: FSMContext
    ) -> None:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            await message.answer("Нужен текст расшифровки. /cancel — отмена.")
            return
        await self._finalize_sample(message, state, text)

    async def _finalize_sample(
        self, message: Message, state: FSMContext, reference_text: str
    ) -> None:
        text = reference_text.strip()
        if len(text) > 1000:
            await message.answer("Текст слишком длинный (макс. 1000 символов).")
            return
        data = await state.get_data()
        mode = (data.get("sample_mode") or "create").strip()
        name = (data.get("profile_name") or "").strip()
        profile_id = (data.get("profile_id") or "").strip()
        audio_path = data.get("audio_path")
        audio_dir = data.get("audio_dir")
        filename = data.get("audio_filename") or "sample_clip.ogg"
        if not audio_path or not Path(audio_path).is_file():
            await self._clear_sample_tmp(state)
            await state.clear()
            await message.answer(
                "Сессия сброшена. Начните снова: /voice_sample или /voice_add_sample"
            )
            return
        if mode == "add" and not profile_id:
            await self._clear_sample_tmp(state)
            await state.clear()
            await message.answer("Нет id модели. /voice_add_sample")
            return
        if mode != "add" and not name:
            await self._clear_sample_tmp(state)
            await state.clear()
            await message.answer("Сессия сброшена. /voice_sample")
            return

        status = await message.answer(
            "Загружаю sample в существующую модель…"
            if mode == "add"
            else "Создаю профиль и загружаю sample…"
        )
        try:
            audio_bytes = Path(audio_path).read_bytes()
            if mode == "add":
                pid = profile_id
                sample = await self._client.add_sample(
                    pid, audio_bytes, filename, text
                )
            else:
                profile = await self._client.create_profile(
                    name,
                    language=config.VOICEBOX_LANGUAGE or "ru",
                    default_engine=config.VOICEBOX_ENGINE or "qwen",
                )
                pid = profile["id"]
                sample = await self._client.add_sample(
                    pid, audio_bytes, filename, text
                )
                name = profile.get("name") or name
        except VoiceboxError as e:
            await self._clear_sample_tmp(state)
            await state.clear()
            await status.edit_text(f"Ошибка Voicebox: {e}")
            return
        except Exception as e:
            logger.exception("voice_sample finalize: %s", e)
            await self._clear_sample_tmp(state)
            await state.clear()
            await status.edit_text(f"Ошибка: {e}")
            return

        await self._clear_sample_tmp(state)
        await state.clear()
        more_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Ещё sample в эту модель",
                        callback_data=f"{CB_ADD_MORE}{pid}",
                    )
                ]
            ]
        )
        title = (
            f"✅ Sample добавлен в <b>{_esc(name)}</b>"
            if mode == "add"
            else f"✅ Модель <b>{_esc(name)}</b> создана"
        )
        await status.edit_text(
            f"{title}.\n"
            f"id: <code>{pid}</code>\n"
            f"sample: <code>{sample.get('id')}</code>\n\n"
            f"Текст sample:\n<i>{_esc(text)}</i>\n\n"
            f"Проверка: <code>/voice_test</code>\n"
            f"Ещё sample: кнопка ниже или <code>/voice_add_sample</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=more_kb,
        )

    async def cmd_voice_test(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        if not await self._guard(message):
            return
        await state.clear()
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            logger.exception("voice_test list: %s", e)
            await message.answer(f"Не удалось получить модели: {e}")
            return
        if not profiles:
            await message.answer("Моделей нет. Сначала /voice_sample")
            return

        text_arg = (command.args or "").strip()
        default_id = (config.VOICEBOX_DEFAULT_PROFILE_ID or "").strip()

        if len(profiles) == 1:
            pid = profiles[0]["id"]
            pname = profiles[0].get("name") or pid
            if text_arg:
                await self._run_test(message, pid, pname, text_arg)
                return
            await state.update_data(profile_id=pid, profile_name=pname)
            await state.set_state(VoiceTestStates.waiting_text)
            await message.answer(
                f"Модель: <b>{_esc(pname)}</b>\n\nПришлите текст для озвучки.",
                parse_mode=ParseMode.HTML,
            )
            return

        if text_arg and default_id and any(p.get("id") == default_id for p in profiles):
            pname = next(
                (p.get("name") for p in profiles if p.get("id") == default_id),
                default_id,
            )
            await self._run_test(message, default_id, pname or default_id, text_arg)
            return

        await state.set_state(VoiceTestStates.waiting_profile)
        if text_arg:
            await state.update_data(pending_text=text_arg)
        rows = []
        for p in profiles[:20]:
            label = p.get("name") or p.get("id") or "?"
            if p.get("id") == default_id:
                label = f"★ {label}"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label[:64],
                        callback_data=f"{CB_PICK}{p['id']}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)]
        )
        await message.answer(
            "Выберите модель для теста:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def on_pick_profile(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только админ", show_alert=True)
            return
        if not self._enabled():
            await callback.answer("Voicebox выключен", show_alert=True)
            return
        data = callback.data or ""
        pid = data[len(CB_PICK) :]
        if not pid:
            await callback.answer("Нет id", show_alert=True)
            return
        try:
            profiles = await self._client.list_profiles()
        except Exception as e:
            await callback.answer(str(e)[:180], show_alert=True)
            return
        profile = next((p for p in profiles if p.get("id") == pid), None)
        if not profile:
            await callback.answer("Модель не найдена", show_alert=True)
            return
        pname = profile.get("name") or pid
        st = await state.get_data()
        pending = (st.get("pending_text") or "").strip()
        await callback.answer()
        if pending and callback.message:
            await state.clear()
            await self._run_test(callback.message, pid, pname, pending)
            return
        await state.update_data(profile_id=pid, profile_name=pname)
        await state.set_state(VoiceTestStates.waiting_text)
        if callback.message:
            await callback.message.answer(
                f"Модель: <b>{_esc(pname)}</b>\n\nПришлите текст для озвучки.",
                parse_mode=ParseMode.HTML,
            )

    async def on_test_text(self, message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            await message.answer("Пришлите текст для озвучки. /cancel — отмена.")
            return
        data = await state.get_data()
        pid = data.get("profile_id")
        pname = data.get("profile_name") or pid
        if not pid:
            await state.clear()
            await message.answer("Сессия сброшена. Начните снова: /voice_test")
            return
        await state.clear()
        await self._run_test(message, pid, pname, text)

    async def _run_test(
        self, message: Message, profile_id: str, profile_name: str, text: str
    ) -> None:
        if len(text) > 2000:
            await message.answer("Текст слишком длинный для теста (макс. ~2000).")
            return
        status = await message.answer(
            f"Генерирую голос «{_esc(profile_name)}»…",
            parse_mode=ParseMode.HTML,
        )
        work: Optional[Path] = None
        try:
            work = Path(tempfile.mkdtemp(prefix="vb_tg_"))
            ogg = await self._client.synthesize_ogg(profile_id, text, work_dir=work)
            voice = BufferedInputFile(ogg.read_bytes(), filename="voice.ogg")
            await message.answer_voice(voice)
            try:
                await status.delete()
            except Exception:
                pass
        except VoiceboxError as e:
            await status.edit_text(f"Ошибка Voicebox: {e}")
        except Exception as e:
            logger.exception("voice_test: %s", e)
            await status.edit_text(f"Ошибка: {e}")
        finally:
            if work and work.exists():
                shutil.rmtree(work, ignore_errors=True)


def _extract_audio(message: Message) -> tuple[Optional[str], str]:
    if message.voice:
        return message.voice.file_id, "sample.ogg"
    if message.audio:
        name = message.audio.file_name or "sample.mp3"
        return message.audio.file_id, name
    if message.document:
        name = message.document.file_name or "sample.bin"
        mime = (message.document.mime_type or "").lower()
        if mime.startswith("audio/") or name.lower().endswith(
            (".ogg", ".opus", ".mp3", ".wav", ".m4a", ".flac", ".aac")
        ):
            return message.document.file_id, name
    return None, ""


def _probe_duration(path: Path) -> float:
    ffmpeg = shutil.which("ffprobe") or "ffprobe"
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "ffprobe failed")[-300:])
    return float((proc.stdout or "0").strip() or 0)


def _trim_to_ogg(src: Path, dst: Path, max_sec: float) -> tuple[float, bool]:
    """Обрезать до max_sec и сохранить OGG Opus. → (итоговая длительность, обрезано?)."""
    duration = _probe_duration(src)
    trimmed = duration > max_sec + 0.05
    take = min(duration, max_sec) if duration > 0 else max_sec
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-t",
        f"{take:.3f}",
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        "-vbr",
        "on",
        "-application",
        "voip",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0 or not dst.is_file():
        raise RuntimeError((proc.stderr or "ffmpeg failed")[-400:])
    out_dur = _probe_duration(dst)
    return out_dur, trimmed


def _clean_transcript(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    low = t.lower()
    if low in {"[тишина]", "[шум, слов не разобрать]", "тишина"}:
        return None
    # Whisper иногда возвращает обёртки из медиапроцессора — не должно,
    # но на всякий случай срежем.
    for prefix in ("[голосовое: ", "[аудио: ", "[кружочек с речью: "):
        if t.startswith(prefix) and t.endswith("]"):
            t = t[len(prefix) : -1].strip()
    return t or None


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
