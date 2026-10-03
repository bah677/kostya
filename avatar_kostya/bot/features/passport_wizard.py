"""ИИ-мастер заполнения паспортов студии в личке Telegram (deep link)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from course.passports import (
    PASSPORT_KINDS,
    build_turn_system,
    filled_count,
    load_passport,
    merge_slots,
    save_passport,
    spec_for,
)
from course.products import active_product_id, product_display_name

logger = logging.getLogger(__name__)

CB = "ppw"
START_PREFIX = "passport_"


class PassportWizardStates(StatesGroup):
    talking = State()


def passport_fill_deeplink(kind: str) -> str:
    """https://t.me/<bot>?start=passport_<kind>"""
    from config import config

    k = (kind or "").strip()
    if k not in PASSPORT_KINDS:
        return ""
    un = (
        str(getattr(config, "TELEGRAM_BOT_USERNAME", "") or "").strip()
        or str(getattr(config, "BIBLIA_BOT_USERNAME", "") or "").strip()
    ).lstrip("@")
    if not un:
        return ""
    return f"https://t.me/{un}?start={START_PREFIX}{k}"


def _kb(kind: str, *, turns: int, max_turns: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Готово — сохранить",
                    callback_data=f"{CB}:done:{kind}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Стоп",
                    callback_data=f"{CB}:stop:{kind}",
                ),
                InlineKeyboardButton(
                    text=f"Ход {turns}/{max_turns}",
                    callback_data=f"{CB}:noop:{kind}",
                ),
            ],
        ]
    )


def _parse_start_kind(param: str) -> Optional[str]:
    raw = (param or "").strip()
    if not raw.startswith(START_PREFIX):
        return None
    kind = raw[len(START_PREFIX) :].strip().lower()
    return kind if kind in PASSPORT_KINDS else None


class PassportWizardFeature(BaseFeature):
    name = "passport_wizard"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        dp.message.register(
            self.cmd_cancel,
            private,
            StateFilter(PassportWizardStates.talking),
            Command("cancel"),
        )
        dp.callback_query.register(
            self.on_callback,
            F.data.startswith(f"{CB}:"),
        )
        self.log("passport wizard handlers registered")

    async def try_start_from_deeplink(
        self, message: Message, state: FSMContext, param: str
    ) -> bool:
        """Вызов из /start: True если это наш payload и мастер запущен."""
        kind = _parse_start_kind(param)
        if not kind:
            return False
        await self.begin(message, state, kind)
        return True

    async def begin(self, message: Message, state: FSMContext, kind: str) -> None:
        uid = int(message.from_user.id) if message.from_user else 0
        if not await is_admin_or_super(self.user_storage, uid):
            await message.answer(
                "Мастер паспортов доступен только администраторам проекта."
            )
            return

        from config import config

        max_turns = int(getattr(config, "PASSPORT_WIZARD_MAX_TURNS", 12) or 12)
        max_turns = max(4, min(30, max_turns))
        passport = await load_passport(self.user_storage, kind)
        await state.clear()
        await state.set_state(PassportWizardStates.talking)
        await state.update_data(
            kind=kind,
            slots=dict(passport.get("slots") or {}),
            turns=0,
            max_turns=max_turns,
            history=[],
        )

        title = passport.get("title") or spec_for(kind)["title"]
        filled, total = filled_count(passport.get("slots") or {})
        product = product_display_name(active_product_id())
        await message.answer(
            f"<b>Мастер: {title}</b>\n"
            f"Проект: <b>{product}</b>\n"
            f"Сейчас заполнено: <b>{filled}/{total}</b>\n\n"
            "Я сначала посмотрю, что уже есть, зачем нужен этот паспорт, "
            "и начну задавать точечные вопросы. Можно остановить кнопкой "
            "«Стоп» или командой /cancel.\n\n"
            f"Лимит: до <b>{max_turns}</b> вопросов — чтобы не зациклиться.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb(kind, turns=0, max_turns=max_turns),
        )
        await self._ai_turn(message, state, user_text="", opening=True)

    async def handle_message(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        data = await state.get_data()
        kind = str(data.get("kind") or "")
        if kind not in PASSPORT_KINDS:
            await state.clear()
            await message.answer("Сессия мастера сброшена. Откройте ссылку снова.")
            return
        uid = int(message.from_user.id) if message.from_user else 0
        if not await is_admin_or_super(self.user_storage, uid):
            await state.clear()
            return
        await self._ai_turn(message, state, user_text=(text or "").strip(), opening=False)

    async def cmd_cancel(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        kind = str(data.get("kind") or "")
        await state.clear()
        title = spec_for(kind)["title"] if kind in PASSPORT_KINDS else "паспорт"
        await message.answer(f"Ок, мастер «{title}» остановлен. Ничего не сохранял из этого хода.")

    async def on_callback(self, query: CallbackQuery, state: FSMContext) -> None:
        parts = (query.data or "").split(":")
        if len(parts) < 3 or parts[0] != CB:
            return
        action, kind = parts[1], parts[2]
        if action == "noop":
            await query.answer("Счётчик ходов мастера")
            return
        uid = int(query.from_user.id) if query.from_user else 0
        if not await is_admin_or_super(self.user_storage, uid):
            await query.answer("Нет доступа", show_alert=True)
            return

        current = await state.get_state()
        if current != PassportWizardStates.talking.state:
            await query.answer("Мастер не активен", show_alert=True)
            return

        data = await state.get_data()
        if str(data.get("kind") or "") != kind:
            await query.answer("Это от другого паспорта", show_alert=True)
            return

        if action == "stop":
            await state.clear()
            if query.message:
                await query.message.answer("Мастер остановлен. Черновик в слотах не трогал.")
            await query.answer("Стоп")
            return

        if action == "done":
            await self._finish(query.message, state, force=True)
            await query.answer("Сохранено")
            return

        await query.answer()

    async def _finish(
        self, message: Optional[Message], state: FSMContext, *, force: bool
    ) -> None:
        data = await state.get_data()
        kind = str(data.get("kind") or "")
        slots = dict(data.get("slots") or {})
        if kind not in PASSPORT_KINDS or message is None:
            await state.clear()
            return
        uid = int(message.chat.id) if message.chat else 0
        filled, total = filled_count(slots)
        done = force or filled >= max(1, total - 1)
        await save_passport(
            self.user_storage, kind, slots, done=done, user_id=uid
        )
        await state.clear()
        title = spec_for(kind)["title"]
        await message.answer(
            f"<b>{title}</b> сохранён.\n"
            f"Заполнено слотов: <b>{filled}/{total}</b>"
            + (" · отмечен готовым" if done else ""),
            parse_mode=ParseMode.HTML,
        )

    async def _project_context(self, kind: str) -> str:
        bits: List[str] = [
            f"Активный продукт: {product_display_name(active_product_id())} (id={active_product_id()})",
            f"Этот паспорт: {spec_for(kind)['title']}",
            f"Зачем: чтобы WRITE-модель в Контент заводе писала в голосе и рамках клуба, "
            f"не выдумывая цены/даты/обещания.",
        ]
        for other in PASSPORT_KINDS:
            if other == kind:
                continue
            try:
                p = await load_passport(self.user_storage, other)
            except Exception:
                continue
            filled, total = filled_count(p.get("slots") or {})
            brief = {
                k: (v[:180] + "…") if len(v) > 180 else v
                for k, v in (p.get("slots") or {}).items()
                if (v or "").strip()
            }
            bits.append(
                f"Соседний паспорт «{p.get('title')}»: {filled}/{total}. "
                f"Суть: {json.dumps(brief, ensure_ascii=False) if brief else 'пусто'}"
            )
        return "\n".join(bits)

    async def _rag_digest(self, kind: str) -> str:
        rag = getattr(self._app, "rag_stack", None) if self._app else None
        if rag is None:
            return ""
        try:
            from rag.scope import scope_from_stack

            gw = scope_from_stack(rag)
            q = str(spec_for(kind).get("rag_query") or "")
            hits = gw.search_chunks(q, k=4) if q else []
            parts = []
            for h in hits[:4]:
                doc = str(h.get("document") or "").strip()
                if doc:
                    parts.append(doc[:700])
            return "\n---\n".join(parts)[:4000]
        except Exception as e:
            logger.warning("passport wizard rag: %s", e)
            return ""

    async def _ai_turn(
        self,
        message: Message,
        state: FSMContext,
        *,
        user_text: str,
        opening: bool,
    ) -> None:
        from config import config
        from course.llm import CourseLLM

        data = await state.get_data()
        kind = str(data.get("kind") or "")
        slots = dict(data.get("slots") or {})
        turns = int(data.get("turns") or 0)
        max_turns = int(data.get("max_turns") or 12)
        history: List[Dict[str, str]] = list(data.get("history") or [])

        if not opening and not user_text:
            await message.answer("Напишите ответ текстом — или нажмите «Стоп».")
            return

        if not opening:
            turns += 1

        model = str(
            getattr(config, "PASSPORT_WIZARD_MODEL", "") or "gpt-4.1"
        ).strip() or "gpt-4.1"

        project_ctx = await self._project_context(kind)
        rag_digest = await self._rag_digest(kind)
        system = build_turn_system(
            kind,
            slots,
            rag_digest,
            project_context=project_ctx,
            turn=turns,
            max_turns=max_turns,
            opening=opening,
        )

        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for row in history[-8:]:
            role = "assistant" if row.get("role") == "assistant" else "user"
            content = str(row.get("text") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        if opening:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Старт мастера. Проанализируй уже заполненные слоты и контекст проекта. "
                        "Коротко скажи, что уже ясно, чего не хватает для работы генерации, "
                        "и задай первый самый важный вопрос."
                    ),
                }
            )
        else:
            messages.append({"role": "user", "content": user_text})

        uid = int(message.from_user.id) if message.from_user else 0
        llm = CourseLLM(self.user_storage)
        tg = self._app.bot if self._app and getattr(self._app, "bot", None) else message.bot

        async def _call() -> Dict[str, Any]:
            return await llm.complete_json(
                model=model,
                messages=messages,
                user_id=uid,
                request_kind="passport_wizard",
            )

        try:
            if tg:
                async with ChatActionSender.typing(message.chat.id, tg):
                    data_out = await _call()
            else:
                data_out = await _call()
        except Exception as e:
            logger.exception("passport wizard llm: %s", e)
            await message.answer(
                f"Модель споткнулась: {e}. Можно ответить ещё раз или нажать «Стоп»."
            )
            return

        reply = str((data_out or {}).get("reply") or "").strip()
        patch = (data_out or {}).get("slots") or {}
        if not isinstance(patch, dict):
            patch = {}
        slots = merge_slots(kind, slots, patch)
        done_flag = bool((data_out or {}).get("done"))

        if user_text:
            history.append({"role": "user", "text": user_text})
        if reply:
            history.append({"role": "assistant", "text": reply})

        await state.update_data(
            slots=slots, turns=turns, history=history[-16:]
        )

        filled, total = filled_count(slots)
        hit_limit = turns >= max_turns
        enough = filled >= total and total > 0

        if reply:
            await message.answer(
                reply,
                reply_markup=_kb(kind, turns=turns, max_turns=max_turns),
            )
        else:
            await message.answer(
                "(пустой ответ модели — напишите ещё раз или «Готово»)",
                reply_markup=_kb(kind, turns=turns, max_turns=max_turns),
            )

        if done_flag or enough or hit_limit:
            note = ""
            if hit_limit and not done_flag and not enough:
                note = (
                    f"\n\nДостигнут лимит {max_turns} вопросов — сохраняю то, что собрали "
                    f"({filled}/{total})."
                )
                if message:
                    await message.answer(note.strip())
            await self._finish(message, state, force=True)
