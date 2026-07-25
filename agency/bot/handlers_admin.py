"""Админ-команды agency-бота: /adm, доступ, апрув рекомендаций, прогон."""

from __future__ import annotations

import html as html_mod
import logging

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import (
    invalidate_admin_cache,
    is_agency_admin,
    is_super_admin,
)
from bot.admin_panel import (
    CB_HOME,
    CB_PREFIX,
    build_admin_panel_group,
    build_admin_panel_home,
    parse_admin_panel_group_cb,
)
from config import Config
from db import admins as admins_db
from db.pool import Pools
from db.repo import AgencyRepo

logger = logging.getLogger(__name__)


async def _require_admin(cfg: Config, pools: Pools, message: Message) -> bool:
    if message.from_user is None or message.from_user.is_bot:
        return False
    ok = await is_agency_admin(cfg, pools.agency, message.from_user.id)
    if not ok:
        if message.chat and message.chat.type == ChatType.PRIVATE:
            await message.answer("⛔ Нет доступа.")
        return False
    return True


async def _require_super(cfg: Config, message: Message) -> bool:
    if message.from_user is None or message.from_user.is_bot:
        return False
    if not is_super_admin(cfg, message.from_user.id):
        await message.answer("⛔ Только супер-админ.")
        return False
    return True


def register_with_ctx(dp: Dispatcher, cfg: Config, pools: Pools, bot) -> None:
    def get_repo() -> AgencyRepo:
        assert pools.agency
        return AgencyRepo(pools.agency)

    private = F.chat.type == ChatType.PRIVATE
    brief_id = int(cfg.AGENCY_BRIEF_CHAT_ID or 0)
    in_brief = F.chat.id == brief_id if brief_id else F.chat.id == 0

    def _chat_ok():
        return private | in_brief

    @dp.message(Command("start", "help"), _chat_ok())
    async def cmd_help(message: Message) -> None:
        if not await _require_admin(cfg, pools, message):
            return
        await message.answer(
            "Agency bot\n"
            "/adm — панель команд\n"
            "/agency_run — прогон с LLM\n"
            "/agency_run_nums — только KPI\n"
            "/agency_recs — рекомендации\n"
            "/agency_gaps — пробелы данных\n"
            "/admins /admin_add /admin_del — только супер-админ"
        )

    @dp.message(Command("adm", "admin"), _chat_ok())
    async def cmd_adm(message: Message) -> None:
        if not await _require_admin(cfg, pools, message):
            return
        text, kb = build_admin_panel_home()
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    @dp.callback_query(F.data.startswith(f"{CB_PREFIX}:"))
    async def cb_adm(cq: CallbackQuery) -> None:
        if cq.from_user is None or not await is_agency_admin(
            cfg, pools.agency, cq.from_user.id
        ):
            await cq.answer("⛔ Нет доступа", show_alert=True)
            return
        if cq.message is None:
            await cq.answer()
            return
        data = cq.data or ""
        try:
            if data == CB_HOME:
                text, kb = build_admin_panel_home()
            else:
                group_key = parse_admin_panel_group_cb(data)
                if not group_key:
                    await cq.answer()
                    return
                text, kb = build_admin_panel_group(group_key)
            await cq.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
            await cq.answer()
        except Exception as e:
            logger.warning("adm panel: %s", e)
            await cq.answer()

    @dp.message(Command("admins"), _chat_ok())
    async def cmd_admins(message: Message) -> None:
        if not await _require_super(cfg, message):
            return
        assert pools.agency
        rows = await admins_db.list_telegram_admin_ids(pools.agency)
        lines = [
            "<b>Admins (БД)</b>",
            f"Супер: <code>{int(cfg.SUPER_ADMIN_ID)}</code>",
        ]
        if cfg.AGENCY_ADMIN_IDS:
            env_ids = ", ".join(f"<code>{i}</code>" for i in cfg.AGENCY_ADMIN_IDS)
            lines.append(f"Env bootstrap: {env_ids}")
        if not rows:
            lines.append("Таблица admins пуста.")
        else:
            for r in rows:
                note = (r.get("note") or "").strip()
                note_part = f" — {html_mod.escape(note)}" if note else ""
                lines.append(
                    f"• <code>{int(r['telegram_user_id'])}</code>{note_part}"
                )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    @dp.message(Command("admin_add"), _chat_ok())
    async def cmd_admin_add(message: Message) -> None:
        if not await _require_super(cfg, message):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await message.reply(
                "Использование: <code>/admin_add 123456789 [note]</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await message.reply("❌ user_id должен быть числом.")
            return
        if is_super_admin(cfg, uid):
            await message.reply("Это уже супер-админ (из .env).")
            return
        note = parts[2].strip() if len(parts) > 2 else ""
        assert pools.agency
        ok = await admins_db.add_telegram_admin_id(
            pools.agency,
            uid,
            note=note,
            created_by=message.from_user.id if message.from_user else None,
        )
        invalidate_admin_cache(uid)
        if ok:
            await message.reply(
                f"✅ Добавлен admin: <code>{uid}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply("❌ Не удалось добавить (см. логи).")

    @dp.message(Command("admin_del"), _chat_ok())
    async def cmd_admin_del(message: Message) -> None:
        if not await _require_super(cfg, message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply(
                "Использование: <code>/admin_del 123456789</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await message.reply("❌ user_id должен быть числом.")
            return
        if is_super_admin(cfg, uid):
            await message.reply("❌ Супер-админа из .env удалить нельзя.")
            return
        assert pools.agency
        ok = await admins_db.remove_telegram_admin_id(pools.agency, uid)
        invalidate_admin_cache(uid)
        if ok:
            await message.reply(
                f"✅ Удалён admin: <code>{uid}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply("❌ Не удалось удалить (см. логи).")

    @dp.message(Command("agency_gaps"), _chat_ok())
    async def cmd_gaps(message: Message) -> None:
        if not await _require_admin(cfg, pools, message):
            return
        gaps = await get_repo().open_gaps(30)
        if not gaps:
            await message.answer("Открытых пробелов нет.")
            return
        lines = [f"• {g['gap_key']} — {g['description'][:180]}" for g in gaps]
        await message.answer("Пробелы:\n" + "\n".join(lines))

    @dp.message(Command("agency_recs"), _chat_ok())
    async def cmd_recs(message: Message) -> None:
        if not await _require_admin(cfg, pools, message):
            return
        recs = await get_repo().list_recommendations(
            statuses=["proposed", "accepted", "shipped"], limit=15
        )
        if not recs:
            await message.answer("Рекомендаций нет.")
            return
        for r in recs:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="accept", callback_data=f"ag:accept:{r['id']}"
                        ),
                        InlineKeyboardButton(
                            text="reject", callback_data=f"ag:reject:{r['id']}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="ship", callback_data=f"ag:ship:{r['id']}"
                        ),
                        InlineKeyboardButton(
                            text="abandon", callback_data=f"ag:abandon:{r['id']}"
                        ),
                    ],
                ]
            )
            text = (
                f"#{r['id']} [{r['status']}] {r['created_on']}\n"
                f"{r['title']}\n"
                f"{(r['body'] or '')[:800]}\n"
                f"{(r['evidence'] or '')[:300]}"
            )
            await message.answer(text, reply_markup=kb)

    @dp.callback_query(F.data.startswith("ag:"))
    async def on_ag_cb(cq: CallbackQuery) -> None:
        if cq.from_user is None or not await is_agency_admin(
            cfg, pools.agency, cq.from_user.id
        ):
            await cq.answer("Нет доступа", show_alert=True)
            return
        parts = (cq.data or "").split(":")
        if len(parts) != 3:
            await cq.answer("bad")
            return
        _, action, rid_s = parts
        try:
            rid = int(rid_s)
        except ValueError:
            await cq.answer("bad id")
            return
        status_map = {
            "accept": "accepted",
            "reject": "rejected",
            "ship": "shipped",
            "abandon": "abandoned",
        }
        status = status_map.get(action)
        if not status:
            await cq.answer("unknown")
            return
        ok = await get_repo().set_recommendation_status(
            rid, status, actor_user_id=cq.from_user.id
        )
        await cq.answer("ok" if ok else "fail")
        if cq.message:
            await cq.message.answer(f"#{rid} → {status}")

    @dp.message(Command("agency_run"), _chat_ok())
    async def cmd_run_llm(message: Message) -> None:
        if not await _require_admin(cfg, pools, message):
            return
        await message.answer("Запускаю…")
        from runtime.orchestrator import run_nightly

        try:
            result = await run_nightly(cfg=cfg, pools=pools, bot=bot, skip_llm=False)
            await message.answer(
                f"Готово: {result.get('status')} run_id={result.get('run_id')}"
            )
        except Exception as e:
            logger.exception("agency_run")
            await message.answer(f"Ошибка: {e}")

    @dp.message(Command("agency_run_nums"), _chat_ok())
    async def cmd_run_nums(message: Message) -> None:
        if not await _require_admin(cfg, pools, message):
            return
        await message.answer("KPI-only…")
        from runtime.orchestrator import run_nightly

        try:
            result = await run_nightly(cfg=cfg, pools=pools, bot=bot, skip_llm=True)
            text = result.get("brief") or str(result)
            if len(text) > 3500:
                text = text[:3500] + "…"
            await message.answer(text)
        except Exception as e:
            logger.exception("agency_run_nums")
            await message.answer(f"Ошибка: {e}")

    @dp.message(private)
    async def cmd_fallback_private(message: Message) -> None:
        if message.from_user and await is_agency_admin(
            cfg, pools.agency, message.from_user.id
        ):
            await message.answer("Неизвестная команда. /adm")
            return
        await message.answer("⛔ Нет доступа.")

    logger.info(
        "agency handlers: super=%s env_admins=%s brief_chat=%s",
        cfg.SUPER_ADMIN_ID,
        cfg.AGENCY_ADMIN_IDS,
        cfg.AGENCY_BRIEF_CHAT_ID or 0,
    )
