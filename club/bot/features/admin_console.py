"""
Админ-консоль в супергруппе основного бота: ответы в топиках поддержки и продаж (legacy Adm).

Хендлеры регистрируются только для chat id = resolved_admin_group_id();
каждый путь проверяет строку в таблице admins.
"""

from __future__ import annotations

import html as html_mod
import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature, FeatureManager
from bot.services.admin_gift_license import execute_admin_gift
from bot.services.mailing_campaign_funnel import (
    collect_mailing_funnel,
    format_campaign_catalog_html,
    format_mailing_funnel_html,
    list_recent_campaigns,
)
from bot.services.ref_key_registry import (
    RK_CB_DISMISS,
    RK_CB_REGISTER,
    RK_CB_TYPE_PREFIX,
    RK_CB_TYPE_SKIP,
    TK_CB_DISMISS,
    TK_CB_REGISTER,
    TK_CB_TYPE_PREFIX,
    TK_CB_TYPE_SKIP,
    flush_pending_ref_key_alerts,
    format_pending_marketing_keys_html,
    parse_ref_key_callback,
    pending_marketing_keys_keyboard,
    ref_key_type_keyboard,
    resolve_ref_key_token,
    sync_orphan_ref_keys_to_pending,
    sync_orphan_touch_keys_to_pending,
)
from bot.services.ref_campaign_funnel import (
    collect_ref_funnel_report,
    format_ref_catalog_html,
    format_ref_funnel_html,
    list_ref_catalog,
    parse_ref_funnel_args,
    resolve_ref_keys,
)
from bot.services.biblia_club_campaign_report import (
    biblia_db_configured,
    collect_biblia_club_campaign_report,
    create_biblia_pool,
    format_biblia_club_campaign_html,
    format_biblia_club_daily_block,
)
from bot.states import AdminGiftStates, AdminRefKeyStates
from bot.services.club_churn_report import ClubChurnReportCollector, load_aboutclub_text
from bot.services.club_report_collect import ClubReportDailyCollector
from bot.services.club_report_v2 import ClubReportV2Collector, build_v2_report_messages
from bot.services.deepseek_churn_analysis import analyze_churn_with_deepseek
from bot.services.followup_leads_report import (
    collect_followup_leads_report,
    format_followup_leads_html,
)
from bot.services.bot_help import build_admin_console_help_html, resolve_help_tier
from bot.services.report_cli import (
    ReportRunOptions,
    format_report_options_hint,
    parse_report_command_args,
)
from bot.services.td_conversion_report import (
    TD_REPORT_PERIOD_DAYS,
    collect_td_conversion_report,
    format_td_conversion_html,
)
from bot.services.excluded_payment_report import (
    EXCLUDED_REPORT_PERIOD_DAYS,
    collect_excluded_payment_report,
    format_excluded_payment_html,
)
from bot.utils.admin_channel import admin_channel_chat_id, send_admin_html_message
from bot.utils.support_ticket_reply_html import format_user_support_ticket_reply_html
from bot.utils.telegram_html import sanitize_telegram_html, split_telegram_html_message_chunks
from config import config
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

_TICKET_PATTERN = re.compile(r"#?(TKT_CL|TKT_BB)[A-Z0-9]+", re.IGNORECASE)
_GRAF_PREFIX = "graf"
_TD_CONV_PREFIX = "td_conv"
_EXCL_PAY_PREFIX = "excl_pay"
_ADMIN_CLEAR_CB = "adm_clear"
# Telegram HTML не поддерживает <hr/> и многие теги — только разделитель текстом.
_REPORT_HTML_PART_SEP = "\n\n━━━━━━━━━━━━━━━━━━━━━\n\n"


class AdminConsoleFeature(BaseFeature):
    """Ответы админов в топиках (поддержка / продажи) + базовые команды."""

    def __init__(
        self,
        user_storage: UserStorage,
        feature_manager: Optional[FeatureManager] = None,
        message_copier=None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self.message_copier = message_copier
        self._bot = None
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._graf_metric_pick: dict[int, str] = {}

    @property
    def name(self) -> str:
        return "admin_console"

    def set_bot(self, telegram_app) -> None:
        self._bot = telegram_app.bot if telegram_app else None

    def register_handlers(self, dp: Dispatcher) -> None:
        admin_private = F.chat.type == ChatType.PRIVATE
        dp.message.register(
            self._cmd_clear_my_chat,
            admin_private,
            Command("clear_my_chat"),
        )
        dp.message.register(
            self._cmd_clear_my_chat,
            admin_private,
            Command("clear_dm"),
        )
        dp.callback_query.register(
            self._cb_clear_my_chat_confirm,
            F.data.startswith(f"{_ADMIN_CLEAR_CB}:"),
        )

        gid = config.resolved_admin_group_id()
        if not gid:
            logger.warning(
                "[%s] Пропуск регистрации: задайте ADMIN_GROUP_ID или числовой ADMIN_CHANNEL_ID",
                self.name,
            )
            return

        admin_chat = F.chat.id == gid

        dp.message.register(self._cmd_admin, admin_chat, Command("admin"))
        dp.message.register(self._cmd_admin, admin_private, Command("admin"))
        dp.message.register(self._cmd_adm, admin_chat, Command("adm"))
        dp.message.register(self._cmd_adm, admin_private, Command("adm"))
        dp.message.register(self._cmd_admins, admin_chat, Command("admins"))
        dp.message.register(self._cmd_admins, admin_private, Command("admins"))
        dp.message.register(self._cmd_admin_add, admin_chat, Command("admin_add"))
        dp.message.register(self._cmd_admin_add, admin_private, Command("admin_add"))
        dp.message.register(self._cmd_admin_del, admin_chat, Command("admin_del"))
        dp.message.register(self._cmd_admin_del, admin_private, Command("admin_del"))
        dp.message.register(self._cmd_report, admin_chat, Command("report"))
        dp.message.register(self._cmd_report, admin_private, Command("report"))
        dp.message.register(self._cmd_graf, admin_chat, Command("graf"))
        dp.message.register(self._cmd_graf, admin_private, Command("graf"))
        dp.message.register(self._cmd_churn_report, admin_chat, Command("churn"))
        dp.message.register(self._cmd_churn_report, admin_private, Command("churn"))
        dp.message.register(self._cmd_churn_report, admin_chat, Command("otval"))
        dp.message.register(self._cmd_churn_report, admin_private, Command("otval"))
        dp.message.register(self._cmd_td_conversion, admin_chat, Command("td"))
        dp.message.register(self._cmd_td_conversion, admin_private, Command("td"))
        dp.message.register(self._cmd_excluded_payment, admin_chat, Command("excluded"))
        dp.message.register(self._cmd_excluded_payment, admin_private, Command("excluded"))
        dp.message.register(self._cmd_gift, admin_chat, Command("gift"))
        dp.message.register(self._cmd_gift, admin_private, Command("gift"))
        dp.message.register(self._cmd_mailing_funnel, admin_chat, Command("mailing_funnel"))
        dp.message.register(
            self._cmd_mailing_funnel, admin_private, Command("mailing_funnel")
        )
        dp.message.register(self._cmd_mail_funnel, admin_chat, Command("mail_funnel"))
        dp.message.register(self._cmd_mail_funnel, admin_private, Command("mail_funnel"))
        dp.message.register(self._cmd_ref_funnel, admin_chat, Command("ref_funnel"))
        dp.message.register(self._cmd_ref_funnel, admin_private, Command("ref_funnel"))
        dp.message.register(self._cmd_ref_funnel, admin_chat, Command("campaign_funnel"))
        dp.message.register(
            self._cmd_ref_funnel, admin_private, Command("campaign_funnel")
        )
        dp.message.register(self._cmd_biblia_club, admin_chat, Command("biblia_club"))
        dp.message.register(
            self._cmd_biblia_club, admin_private, Command("biblia_club")
        )
        dp.message.register(self._cmd_ref_key, admin_chat, Command("ref_key"))
        dp.message.register(self._cmd_ref_key, admin_private, Command("ref_key"))
        dp.message.register(self._cmd_touch_key, admin_chat, Command("touch_key"))
        dp.message.register(self._cmd_touch_key, admin_private, Command("touch_key"))
        dp.message.register(
            self._cmd_ref_key_name,
            admin_chat,
            AdminRefKeyStates.waiting_name,
            F.text,
        )
        dp.message.register(
            self._cmd_ref_key_name,
            admin_private,
            AdminRefKeyStates.waiting_name,
            F.text,
        )
        dp.callback_query.register(
            self._cb_ref_key_action,
            F.data.startswith(RK_CB_REGISTER)
            | F.data.startswith(RK_CB_DISMISS)
            | F.data.startswith(RK_CB_TYPE_PREFIX),
        )
        dp.callback_query.register(
            self._cb_touch_key_action,
            F.data.startswith(TK_CB_REGISTER)
            | F.data.startswith(TK_CB_DISMISS)
            | F.data.startswith(TK_CB_TYPE_PREFIX),
        )
        dp.message.register(
            self._cmd_followup_leads, admin_chat, Command("followup_leads")
        )
        dp.message.register(
            self._cmd_followup_leads, admin_private, Command("followup_leads")
        )
        dp.message.register(
            self._cmd_followup_leads, admin_chat, Command("dozhim_leads")
        )
        dp.message.register(
            self._cmd_followup_leads, admin_private, Command("dozhim_leads")
        )
        dp.message.register(
            self._cmd_gift_days,
            admin_chat,
            AdminGiftStates.waiting_days,
            F.text,
        )
        dp.message.register(
            self._cmd_gift_days,
            admin_private,
            AdminGiftStates.waiting_days,
            F.text,
        )
        dp.callback_query.register(
            self._graf_callback_handler,
            F.data.startswith(f"{_GRAF_PREFIX}:"),
            F.message,
        )
        dp.callback_query.register(
            self._td_conv_callback_handler,
            F.data.startswith(f"{_TD_CONV_PREFIX}:"),
            F.message,
        )
        dp.callback_query.register(
            self._excl_pay_callback_handler,
            F.data.startswith(f"{_EXCL_PAY_PREFIX}:"),
            F.message,
        )

        if config.SUPPORT_THREAD_ID > 0:
            dp.message.register(
                self._support_thread_reply,
                admin_chat,
                F.message_thread_id == config.SUPPORT_THREAD_ID,
                F.reply_to_message,
                F.text,
            )

        if config.ADMIN_DIALOG_THREAD_ID > 0:
            dp.message.register(
                self._sales_thread_reply,
                admin_chat,
                F.message_thread_id == config.ADMIN_DIALOG_THREAD_ID,
                F.reply_to_message,
                F.text,
            )

        if config.DIALOG_FORUM_GROUP_ID:
            forum_chat = F.chat.id == config.DIALOG_FORUM_GROUP_ID
            dp.message.register(
                self._forum_topic_reply,
                forum_chat,
                F.reply_to_message,
                F.text,
            )
            logger.info(
                "[%s] Хендлер ответов в форумной группе зарегистрирован для chat_id=%s",
                self.name,
                config.DIALOG_FORUM_GROUP_ID,
            )

        logger.info("[%s] Зарегистрированы хендлеры для chat_id=%s", self.name, gid)

    async def initialize(self) -> None:
        await super().initialize()
        if admin_channel_chat_id() is None:
            logger.info(
                "[%s] Cron отчёта пропуск: ADMIN_CHANNEL_ID не задан или не числовой id",
                self.name,
            )
            return
        if self._bot is None:
            logger.info("[%s] Cron отчёта пропуск: бот без set_bot", self.name)
            return
        if self.user_storage.pool is None:
            logger.info("[%s] Cron отчёта пропуск: пул БД не открыт", self.name)
            return
        try:
            sched = AsyncIOScheduler(timezone="Europe/Moscow")
            sched.add_job(
                self._club_report_scheduled_tick,
                CronTrigger(
                    hour=config.REPORT_HOUR,
                    minute=config.REPORT_MINUTE,
                    timezone="Europe/Moscow",
                ),
                id="club_daily_admin_report",
                replace_existing=True,
            )
            sched.start()
            self._scheduler = sched
            logger.info(
                "[%s] Клубный отчёт по расписанию — %02d:%02d Europe/Moscow → ADMIN канал",
                self.name,
                config.REPORT_HOUR,
                config.REPORT_MINUTE,
            )
            import asyncio

            asyncio.create_task(self._ref_key_pending_startup())
        except Exception as e:
            logger.warning("[%s] Планировщик отчёта не запущен: %s", self.name, e)

    async def teardown(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                logger.exception("[%s] Ошибка остановки планировщика отчёта", self.name)
            self._scheduler = None

    async def _club_report_scheduled_tick(self) -> None:
        try:
            if self._bot is None or admin_channel_chat_id() is None:
                return
            if self.user_storage.pool is None:
                return
            tid = config.CLUB_REPORT_THREAD_ID if config.CLUB_REPORT_THREAD_ID > 0 else None

            if config.REPORT_LEGACY_ENABLED:
                collector = ClubReportDailyCollector(
                    self.user_storage.pool,
                    club_group_id=config.CLUB_GROUP_ID,
                )
                metrics = await collector.get_all_metrics()
                report_html = collector.format_report(metrics)
                await self._save_report_snapshot(
                    metrics=metrics,
                    report_html=report_html,
                    source="cron_legacy",
                )
                ok_sent = await send_admin_html_message(
                    self._bot,
                    report_html,
                    thread_id=tid,
                )
                if ok_sent:
                    logger.info("[%s] Legacy-отчёт отправлен", self.name)
                else:
                    logger.warning("[%s] Legacy-отчёт не отправлен", self.name)

            v2 = ClubReportV2Collector(
                self.user_storage.pool,
                club_group_id=config.CLUB_GROUP_ID,
                user_storage=self.user_storage,
            )
            include_llm = config.CLUB_REPORT_INCLUDE_DEEPSEEK
            metrics_v2 = await v2.collect_all(include_llm=include_llm)
            v2_parts = build_v2_report_messages(metrics_v2, include_llm=include_llm)
            v2_ok = 0
            for part in v2_parts:
                if await send_admin_html_message(self._bot, part, thread_id=tid):
                    v2_ok += 1
            if v2_ok == len(v2_parts):
                logger.info("[%s] Отчёт v2 (%s сообщ.) отправлен", self.name, v2_ok)
            else:
                logger.warning(
                    "[%s] Отчёт v2 отправлен частично: %s/%s",
                    self.name,
                    v2_ok,
                    len(v2_parts),
                )
            try:
                await self._save_report_snapshot(
                    metrics=metrics_v2,
                    report_html=_REPORT_HTML_PART_SEP.join(v2_parts),
                    source="cron_v2",
                )
            except Exception as e:
                logger.error(
                    "[%s] Снимок отчёта v2 не сохранён (отчёт уже отправлен): %s",
                    self.name,
                    e,
                    exc_info=True,
                )

            if config.CLUB_OUTREACH_DM_ENABLED or config.CLUB_OUTREACH_DAILY_LIMIT:
                from bot.services.club_engagement_report import (
                    build_engagement_report_html,
                )
                from bot.services.club_llm_token_report import (
                    build_llm_token_report_html,
                )

                eng_html = await build_engagement_report_html(
                    self.user_storage.pool,
                    self.user_storage,
                    api_key=(config.DEEPSEEK_API_KEY or "").strip(),
                )
                tok_html = await build_llm_token_report_html(self.user_storage)
                for extra in (eng_html, tok_html):
                    if extra and await send_admin_html_message(
                        self._bot, extra, thread_id=tid
                    ):
                        logger.info("[%s] Extra midnight report chunk sent", self.name)
        except Exception as e:
            logger.exception("[%s] Ошибка при отправке отчёта по расписанию: %s", self.name, e)

    def _is_super_admin_user_id(self, uid: int) -> bool:
        sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
        return bool(sid) and uid == sid

    async def _ensure_console_admin(self, message: Message, *, allow_private: bool = False) -> bool:
        if message.chat.type != ChatType.SUPERGROUP and not (
            allow_private and message.chat.type == ChatType.PRIVATE
        ):
            return False
        if message.from_user is None or message.from_user.is_bot:
            return False
        uid = message.from_user.id
        in_table = await is_telegram_admin(self.user_storage, uid)
        if not in_table and not self._is_super_admin_user_id(uid):
            await message.reply(
                "⛔ Нет доступа. Нужна строка в <code>admins</code> или "
                "<code>SUPER_ADMIN_ID</code> в .env с вашим Telegram ID.",
                parse_mode=ParseMode.HTML,
            )
            return False
        return True

    async def _cmd_admin(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        tier = await resolve_help_tier(self.user_storage, message.from_user.id)
        text = build_admin_console_help_html(
            tier,
            report_hint=format_report_options_hint(),
        )
        await message.answer(text, parse_mode=ParseMode.HTML)

    async def _cmd_adm(self, message: Message) -> None:
        await self._cmd_admin(message)

    async def _ensure_super_admin(self, message: Message) -> bool:
        """Управление таблицей admins — только user_id из SUPER_ADMIN_ID (без строки в БД)."""
        if message.chat.type != ChatType.SUPERGROUP and message.chat.type != ChatType.PRIVATE:
            return False
        if message.from_user is None or message.from_user.is_bot:
            return False
        if not getattr(config, "SUPER_ADMIN_ID", 0):
            await message.reply("⛔ SUPER_ADMIN_ID не задан в .env.")
            return False
        if not self._is_super_admin_user_id(message.from_user.id):
            await message.reply("⛔ Доступно только супер-админу (SUPER_ADMIN_ID).")
            return False
        return True

    async def _cmd_admins(self, message: Message) -> None:
        if not await self._ensure_super_admin(message):
            return
        rows = await self.user_storage.list_telegram_admin_ids()
        if not rows:
            await message.answer("Список admins пуст.")
            return
        lines = ["<b>Admins:</b>"]
        for r in rows:
            note = (r.get("note") or "").strip()
            note_part = f" — {html_mod.escape(note)}" if note else ""
            lines.append(f"• <code>{int(r['telegram_user_id'])}</code>{note_part}")
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _cmd_admin_add(self, message: Message) -> None:
        if not await self._ensure_super_admin(message):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 2:
            await message.reply("Использование: <code>/admin_add 123456789 [note]</code>", parse_mode=ParseMode.HTML)
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await message.reply("❌ user_id должен быть числом.")
            return
        note = parts[2].strip() if len(parts) > 2 else ""
        ok = await self.user_storage.add_telegram_admin_id(uid, note=note)
        if ok:
            await message.reply(f"✅ Добавлен admin: <code>{uid}</code>", parse_mode=ParseMode.HTML)
        else:
            await message.reply("❌ Не удалось добавить admin (см. логи).")

    async def _cmd_admin_del(self, message: Message) -> None:
        if not await self._ensure_super_admin(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Использование: <code>/admin_del 123456789</code>", parse_mode=ParseMode.HTML)
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await message.reply("❌ user_id должен быть числом.")
            return
        ok = await self.user_storage.remove_telegram_admin_id(uid)
        if ok:
            await message.reply(f"✅ Удалён admin: <code>{uid}</code>", parse_mode=ParseMode.HTML)
        else:
            await message.reply("❌ Не удалось удалить admin (см. логи).")

    async def _build_biblia_club_daily_block(self) -> Optional[str]:
        if not biblia_db_configured(config):
            return None
        pool = self.user_storage.pool
        if pool is None:
            return None
        biblia_pool = None
        try:
            biblia_pool = await create_biblia_pool(config)
            bot_username = (config.TELEGRAM_BOT_USERNAME or "Talk_God_Bot").lstrip("@")
            report = await collect_biblia_club_campaign_report(
                pool,
                biblia_pool,
                bot_username=bot_username,
            )
            return format_biblia_club_daily_block(report) or None
        finally:
            if biblia_pool is not None:
                await biblia_pool.close()

    async def _build_report_parts(self, opts: ReportRunOptions) -> list[str]:
        if opts.biblia_club_only:
            block = await self._build_biblia_club_daily_block()
            return [block] if block else []

        include_legacy = (
            opts.include_legacy
            if opts.include_legacy is not None
            else bool(config.REPORT_LEGACY_ENABLED)
        )
        parts: list[str] = []
        if include_legacy:
            collector = ClubReportDailyCollector(
                self.user_storage.pool,
                club_group_id=config.CLUB_GROUP_ID,
            )
            metrics = await collector.get_all_metrics()
            parts.append(collector.format_report(metrics))
        if opts.include_v2:
            v2 = ClubReportV2Collector(
                self.user_storage.pool,
                club_group_id=config.CLUB_GROUP_ID,
                user_storage=self.user_storage,
            )
            metrics_v2 = await v2.collect_all(
                include_llm=opts.include_llm and config.CLUB_REPORT_INCLUDE_DEEPSEEK
            )
            parts.extend(
                build_v2_report_messages(
                    metrics_v2,
                    include_llm=opts.include_llm and config.CLUB_REPORT_INCLUDE_DEEPSEEK,
                )
            )
        return parts

    async def _cmd_report(self, message: Message, command: CommandObject) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        opts = parse_report_command_args(command.args)
        try:
            parts = await self._build_report_parts(opts)
        except Exception as e:
            logger.exception("report collector failed: %s", e)
            await self._dm_err(message.from_user.id, f"Ошибка сборки отчёта: {e}")
            return
        if not parts:
            if opts.biblia_club_only:
                await message.reply(
                    "Нечего отправить: нет данных или не настроена БД Библии "
                    "(<code>BIBLIA_DB_*</code> в .env).",
                    parse_mode=ParseMode.HTML,
                )
                return
            await message.reply(
                "Нечего отправить: отключены и v2, и legacy. "
                "Проверьте аргументы или <code>REPORT_LEGACY_ENABLED</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        mode_bits: list[str] = []
        if opts.biblia_club_only:
            mode_bits.append("Библия → Клуб")
        if opts.include_v2:
            mode_bits.append("v2" + ("" if opts.include_llm else ", без DeepSeek"))
        if opts.include_legacy is False:
            pass
        elif opts.include_legacy or (
            opts.include_legacy is None and config.REPORT_LEGACY_ENABLED
        ):
            mode_bits.append("legacy")
        mode_note = ", ".join(mode_bits) if mode_bits else "отчёт"
        if opts.biblia_club_only:
            header = (
                f"📊 <b>Библия → Клуб</b>\n"
                f"<i>Сформирован по /report ({html_mod.escape(mode_note)})</i>\n\n"
            )
        else:
            header = (
                f"📊 <b>Клубный отчёт</b>\n"
                f"<i>Сформирован по /report ({html_mod.escape(mode_note)})</i>\n\n"
            )
        for i, part in enumerate(parts):
            await self._send_html_in_dm_chunks(
                user_id=message.from_user.id,
                html_text=part,
                header=header if i == 0 else "",
            )

    async def _cmd_mail_funnel(
        self, message: Message, command: CommandObject
    ) -> None:
        await self._cmd_mailing_funnel(message, command)

    async def _cmd_mailing_funnel(
        self, message: Message, command: CommandObject
    ) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        pool = self.user_storage.pool
        if pool is None:
            await message.reply("❌ База данных недоступна.")
            return

        raw = (command.args or "").replace(",", " ").split()
        if not raw:
            campaigns = await list_recent_campaigns(pool, limit=25)
            text = format_campaign_catalog_html(campaigns)
            await self._send_html_in_dm_chunks(
                user_id=message.from_user.id,
                html_text=text,
                header="",
            )
            return

        ids: list[int] = []
        for part in raw:
            try:
                ids.append(int(part))
            except ValueError:
                await message.reply(
                    f"❌ Не число: <code>{html_mod.escape(part)}</code>. "
                    "Пример: <code>/mailing_funnel 12 15 20</code>",
                    parse_mode=ParseMode.HTML,
                )
                return

        rows = await collect_mailing_funnel(pool, ids)
        text = format_mailing_funnel_html(rows, requested_ids=ids)
        await self._send_html_in_dm_chunks(
            user_id=message.from_user.id,
            html_text=text,
            header="",
        )

    async def _cmd_biblia_club(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        if not biblia_db_configured(config):
            await message.reply(
                "❌ Не настроена БД Библии (<code>BIBLIA_DB_*</code> в .env).",
                parse_mode=ParseMode.HTML,
            )
            return
        pool = self.user_storage.pool
        if pool is None:
            await message.reply("❌ База данных клуба недоступна.")
            return
        biblia_pool = None
        try:
            biblia_pool = await create_biblia_pool(config)
            bot_username = (config.TELEGRAM_BOT_USERNAME or "Talk_God_Bot").lstrip("@")
            report = await collect_biblia_club_campaign_report(
                pool,
                biblia_pool,
                bot_username=bot_username,
            )
            text = format_biblia_club_campaign_html(report)
        except Exception as e:
            logger.exception("[%s] biblia_club report failed: %s", self.name, e)
            await message.reply(
                "❌ Не удалось собрать отчёт Библия→Клуб. Подробности в логах.",
                parse_mode=ParseMode.HTML,
            )
            return
        finally:
            if biblia_pool is not None:
                await biblia_pool.close()
        await self._send_html_in_dm_chunks(
            user_id=message.from_user.id,
            html_text=text,
            header="",
        )

    async def _cmd_followup_leads(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        pool = self.user_storage.pool
        if pool is None:
            await message.reply("❌ База данных недоступна.")
            return
        try:
            report = await collect_followup_leads_report(pool)
            text = format_followup_leads_html(report)
        except Exception as e:
            logger.exception("[%s] followup_leads report failed: %s", self.name, e)
            await message.reply(
                "❌ Не удалось собрать отчёт. Подробности в логах бота.",
                parse_mode=ParseMode.HTML,
            )
            return
        await self._send_html_in_dm_chunks(
            user_id=message.from_user.id,
            html_text=text,
            header="",
        )

    async def _cmd_ref_funnel(
        self, message: Message, command: CommandObject
    ) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        pool = self.user_storage.pool
        if pool is None:
            await message.reply("❌ База данных недоступна.")
            return

        parsed = parse_ref_funnel_args(command.args)
        has_args = bool(parsed.explicit_keys or parsed.filters.type_filter or parsed.filters.search_filter)

        if not has_args:
            catalog = await list_ref_catalog(pool, days=30)
            text = format_ref_catalog_html(catalog)
            await self._send_html_in_dm_chunks(
                user_id=message.from_user.id,
                html_text=text,
                header="",
            )
            return

        ref_keys = await resolve_ref_keys(
            pool,
            parsed.explicit_keys,
            parsed.filters,
        )
        if not ref_keys:
            await message.reply(
                "❌ Не найдено ref_key по аргументам. "
                "Без аргументов — <code>/ref_funnel</code> (каталог).",
                parse_mode=ParseMode.HTML,
            )
            return

        rows, total, n_keys = await collect_ref_funnel_report(pool, ref_keys)
        text = format_ref_funnel_html(
            rows,
            ref_keys=ref_keys,
            total=total,
            keys_requested=n_keys,
        )
        await self._send_html_in_dm_chunks(
            user_id=message.from_user.id,
            html_text=text,
            header="",
        )

    async def _ref_key_pending_startup(self) -> None:
        try:
            if self._bot is None or self.user_storage.pool is None:
                return
            synced_ref = await sync_orphan_ref_keys_to_pending(self.user_storage)
            synced_touch = await sync_orphan_touch_keys_to_pending(self.user_storage)
            sent = await flush_pending_ref_key_alerts(
                self.user_storage, self._bot, limit=5
            )
            if synced_ref or synced_touch or sent:
                logger.info(
                    "[%s] marketing keys pending: ref_sync=%s touch_sync=%s alerts_sent=%s",
                    self.name,
                    synced_ref,
                    synced_touch,
                    sent,
                )
        except Exception as e:
            logger.warning("[%s] ref_key pending startup: %s", self.name, e)

    async def _cmd_ref_key(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return

        raw = (command.args or "").strip()
        if not raw:
            ref_rows = await self.user_storage.list_ref_key_pending()
            touch_rows = await self.user_storage.list_touch_key_pending()
            text = format_pending_marketing_keys_html(ref_rows, touch_rows)
            kb = pending_marketing_keys_keyboard(ref_rows, touch_rows)
            kb = kb if (ref_rows or touch_rows) else None
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return

        ref_key = raw.split()[0].strip()
        if ref_key.startswith("ref_"):
            ref_key = ref_key[4:]
        await self._begin_ref_key_naming(message, state, ref_key)

    async def _cmd_touch_key(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return

        raw = (command.args or "").strip()
        if not raw:
            ref_rows = await self.user_storage.list_ref_key_pending()
            touch_rows = await self.user_storage.list_touch_key_pending()
            text = format_pending_marketing_keys_html(ref_rows, touch_rows)
            kb = pending_marketing_keys_keyboard(ref_rows, touch_rows)
            kb = kb if (ref_rows or touch_rows) else None
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return

        touch_key = raw.split()[0].strip()
        await self._begin_touch_key_naming(message, state, touch_key)

    async def _begin_ref_key_naming(
        self, message: Message, state: FSMContext, ref_key: str
    ) -> None:
        key = (ref_key or "").strip()
        if not key:
            await message.reply("❌ Пустой ref_key.")
            return
        if await self.user_storage.ref_key_exists(key):
            name = await self.user_storage.get_ref_key_name(key)
            await message.reply(
                f"ℹ️ Ключ <code>{html_mod.escape(key)}</code> уже есть: "
                f"<b>{html_mod.escape(name or '—')}</b>",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.set_state(AdminRefKeyStates.waiting_name)
        await state.update_data(
            marketing_key_kind="ref",
            ref_key=key,
            touch_key=None,
            ref_key_types=[],
        )
        await message.reply(
            f"🏷 <b>Псевдоним для ref</b> <code>{html_mod.escape(key)}</code>\n\n"
            "Введите короткое название кампании (как в отчётах и уведомлениях об оплате):",
            parse_mode=ParseMode.HTML,
        )

    async def _begin_touch_key_naming(
        self, message: Message, state: FSMContext, touch_key: str
    ) -> None:
        key = (touch_key or "").strip()
        if not key:
            await message.reply("❌ Пустой touch_key.")
            return
        if await self.user_storage.touch_key_label_exists(key):
            name = await self.user_storage.get_touch_key_label_name(key)
            await message.reply(
                f"ℹ️ Колбэк <code>{html_mod.escape(key)}</code> уже есть: "
                f"<b>{html_mod.escape(name or '—')}</b>",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.set_state(AdminRefKeyStates.waiting_name)
        await state.update_data(
            marketing_key_kind="touch",
            touch_key=key,
            ref_key=None,
            ref_key_types=[],
        )
        short = key if len(key) <= 120 else key[:117] + "…"
        await message.reply(
            f"🏷 <b>Псевдоним для колбэка</b>\n"
            f"<code>{html_mod.escape(short)}</code>\n\n"
            "Введите название (источник оплаты в уведомлениях):",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_ref_key_name(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            await state.clear()
            return
        if message.from_user is None:
            await state.clear()
            return

        name = (message.text or "").strip()
        if len(name) < 2:
            await message.reply("Название слишком короткое. Введите ещё раз.")
            return

        data = await state.get_data()
        kind = (data.get("marketing_key_kind") or "ref").strip()
        ref_key = (data.get("ref_key") or "").strip()
        touch_key = (data.get("touch_key") or "").strip()
        active_key = touch_key if kind == "touch" else ref_key
        if not active_key:
            await state.clear()
            await message.reply(
                "❌ Сессия сброшена. <code>/ref_key KEY</code> или "
                "<code>/touch_key CALLBACK</code>."
            )
            return

        types = await self.user_storage.list_touch_key_label_types()
        await state.update_data(ref_key_name=name, ref_key_types=types)
        if types:
            label = "колбэка" if kind == "touch" else "ref"
            short = html_mod.escape(
                active_key if len(active_key) <= 80 else active_key[:77] + "…"
            )
            await message.reply(
                f"Тип канала для {label} <code>{short}</code> "
                f"(«{html_mod.escape(name)}»):",
                parse_mode=ParseMode.HTML,
                reply_markup=ref_key_type_keyboard(
                    types,
                    type_prefix=TK_CB_TYPE_PREFIX if kind == "touch" else RK_CB_TYPE_PREFIX,
                    skip_data=TK_CB_TYPE_SKIP if kind == "touch" else RK_CB_TYPE_SKIP,
                ),
            )
            return

        ok = False
        if kind == "touch":
            ok = await self.user_storage.create_touch_key_label_entry(
                touch_key, name
            )
        else:
            ok = await self.user_storage.create_ref_key_entry(ref_key, name)
        await state.clear()
        if ok:
            short_key = html_mod.escape(
                active_key if len(active_key) <= 80 else active_key[:77] + "…"
            )
            await message.reply(
                f"✅ Сохранено: <code>{short_key}</code> → "
                f"<b>{html_mod.escape(name)}</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply("❌ Не удалось сохранить.")

    async def _cb_ref_key_action(self, query: CallbackQuery, state: FSMContext) -> None:
        if query.from_user is None:
            await query.answer()
            return
        if not await is_telegram_admin(self.user_storage, query.from_user.id):
            if not self._is_super_admin_user_id(query.from_user.id):
                await query.answer("Нет доступа", show_alert=True)
                return

        data = query.data or ""

        if data.startswith(RK_CB_DISMISS):
            token = parse_ref_key_callback(data, RK_CB_DISMISS)
            ref_key = await resolve_ref_key_token(self.user_storage, token or "")
            if ref_key and await self.user_storage.dismiss_ref_key_pending(ref_key):
                await query.answer("Скрыто")
                if query.message:
                    await query.message.edit_reply_markup(reply_markup=None)
            else:
                await query.answer("Не найдено", show_alert=True)
            return

        if data.startswith(RK_CB_REGISTER):
            token = parse_ref_key_callback(data, RK_CB_REGISTER)
            ref_key = await resolve_ref_key_token(self.user_storage, token or "")
            if not ref_key or not query.message:
                await query.answer("Не найдено", show_alert=True)
                return
            await query.answer()
            await self._begin_ref_key_naming(query.message, state, ref_key)
            return

        if data == RK_CB_TYPE_SKIP or data.startswith(RK_CB_TYPE_PREFIX):
            fsm = await state.get_data()
            kind = (fsm.get("marketing_key_kind") or "ref").strip()
            ref_key = (fsm.get("ref_key") or "").strip()
            touch_key = (fsm.get("touch_key") or "").strip()
            name = (fsm.get("ref_key_name") or "").strip()
            types: list = fsm.get("ref_key_types") or []
            active_key = touch_key if kind == "touch" else ref_key
            if not active_key or not name:
                await query.answer("Сначала введите название", show_alert=True)
                return

            type_label = None
            if data.startswith(RK_CB_TYPE_PREFIX) and data != RK_CB_TYPE_SKIP:
                try:
                    idx = int(data[len(RK_CB_TYPE_PREFIX) :])
                    type_label = types[idx] if 0 <= idx < len(types) else None
                except ValueError:
                    type_label = None

            if kind == "touch":
                ok = await self.user_storage.create_touch_key_label_entry(
                    touch_key, name, type_label=type_label
                )
            else:
                ok = await self.user_storage.create_ref_key_entry(
                    ref_key, name, type_label=type_label
                )
            await state.clear()
            await query.answer("Сохранено" if ok else "Ошибка", show_alert=not ok)
            if query.message and ok:
                short = html_mod.escape(
                    active_key if len(active_key) <= 80 else active_key[:77] + "…"
                )
                await query.message.reply(
                    f"✅ <code>{short}</code> → "
                    f"<b>{html_mod.escape(name)}</b>"
                    + (
                        f" · {html_mod.escape(type_label)}"
                        if type_label
                        else ""
                    ),
                    parse_mode=ParseMode.HTML,
                )
            return

        await query.answer()

    async def _cb_touch_key_action(self, query: CallbackQuery, state: FSMContext) -> None:
        if query.from_user is None:
            await query.answer()
            return
        if not await is_telegram_admin(self.user_storage, query.from_user.id):
            if not self._is_super_admin_user_id(query.from_user.id):
                await query.answer("Нет доступа", show_alert=True)
                return

        data = query.data or ""

        if data.startswith(TK_CB_DISMISS):
            try:
                pending_id = int(data[len(TK_CB_DISMISS) :])
            except ValueError:
                await query.answer("Не найдено", show_alert=True)
                return
            if await self.user_storage.dismiss_touch_key_pending(pending_id):
                await query.answer("Скрыто")
                if query.message:
                    await query.message.edit_reply_markup(reply_markup=None)
            else:
                await query.answer("Не найдено", show_alert=True)
            return

        if data.startswith(TK_CB_REGISTER):
            try:
                pending_id = int(data[len(TK_CB_REGISTER) :])
            except ValueError:
                await query.answer("Не найдено", show_alert=True)
                return
            row = await self.user_storage.get_touch_key_pending_row(pending_id)
            if not row or not query.message:
                await query.answer("Не найдено", show_alert=True)
                return
            await query.answer()
            await self._begin_touch_key_naming(
                query.message, state, str(row["touch_key"])
            )
            return

        if data == TK_CB_TYPE_SKIP or data.startswith(TK_CB_TYPE_PREFIX):
            fsm = await state.get_data()
            touch_key = (fsm.get("touch_key") or "").strip()
            name = (fsm.get("ref_key_name") or "").strip()
            types: list = fsm.get("ref_key_types") or []
            if not touch_key or not name:
                await query.answer("Сначала введите название", show_alert=True)
                return

            type_label = None
            if data.startswith(TK_CB_TYPE_PREFIX) and data != TK_CB_TYPE_SKIP:
                try:
                    idx = int(data[len(TK_CB_TYPE_PREFIX) :])
                    type_label = types[idx] if 0 <= idx < len(types) else None
                except ValueError:
                    type_label = None

            ok = await self.user_storage.create_touch_key_label_entry(
                touch_key, name, type_label=type_label
            )
            await state.clear()
            await query.answer("Сохранено" if ok else "Ошибка", show_alert=not ok)
            if query.message and ok:
                short = html_mod.escape(
                    touch_key if len(touch_key) <= 80 else touch_key[:77] + "…"
                )
                await query.message.reply(
                    f"✅ <code>{short}</code> → "
                    f"<b>{html_mod.escape(name)}</b>"
                    + (
                        f" · {html_mod.escape(type_label)}"
                        if type_label
                        else ""
                    ),
                    parse_mode=ParseMode.HTML,
                )
            return

        await query.answer()

    async def _cmd_gift(
        self, message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None or not self._bot:
            return

        raw = (command.args or "").strip().split()
        if not raw:
            await message.reply(
                "🎁 <b>Лицензия в подарок</b>\n\n"
                "Формат: <code>/gift USER_ID</code> — затем число дней\n"
                "или <code>/gift USER_ID ДНЕЙ</code> сразу.\n\n"
                "Клуб: «Любящие Бога».",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            target_uid = int(raw[0])
        except ValueError:
            await message.reply("❌ USER_ID должен быть числом (Telegram ID).")
            return
        if target_uid <= 0:
            await message.reply("❌ USER_ID должен быть положительным.")
            return

        if len(raw) >= 2:
            try:
                days = int(raw[1])
            except ValueError:
                await message.reply("❌ Дней должно быть целое число.")
                return
            reply = await execute_admin_gift(
                user_storage=self.user_storage,
                bot=self._bot,
                feature_manager=self.feature_manager,
                message_copier=self.message_copier,
                admin_user=message.from_user,
                target_user_id=target_uid,
                days=days,
            )
            await message.reply(reply, parse_mode=ParseMode.HTML)
            return

        await state.set_state(AdminGiftStates.waiting_days)
        await state.update_data(gift_target_user_id=target_uid)
        await message.reply(
            f"🎁 Выдать доступ пользователю <code>{target_uid}</code>.\n\n"
            "На сколько дней? (число от 1 до 3650)",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_gift_days(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            await state.clear()
            return
        if message.from_user is None or not self._bot:
            await state.clear()
            return

        text = (message.text or "").strip()
        if not text.isdigit():
            await message.reply("Введите число дней (например, 30).")
            return
        days = int(text)
        data = await state.get_data()
        target_uid = data.get("gift_target_user_id")
        await state.clear()
        if not target_uid:
            await message.reply("❌ Сессия сброшена. Начните снова: /gift USER_ID")
            return

        reply = await execute_admin_gift(
            user_storage=self.user_storage,
            bot=self._bot,
            feature_manager=self.feature_manager,
            message_copier=self.message_copier,
            admin_user=message.from_user,
            target_user_id=int(target_uid),
            days=days,
        )
        await message.reply(reply, parse_mode=ParseMode.HTML)

    async def _cmd_clear_my_chat(self, message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.reply("Команда работает только в личке с ботом.")
            return
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None:
            return
        uid = message.from_user.id
        n = await self.user_storage.count_private_chat_messages(uid)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, удалить всё",
                        callback_data=f"{_ADMIN_CLEAR_CB}:yes:{uid}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"{_ADMIN_CLEAR_CB}:no:{uid}",
                    )
                ],
            ]
        )
        await message.answer(
            "<b>⚠️ Удаление истории в личке</b>\n\n"
            f"Будет очищена <b>ваша</b> переписка с ботом в базе "
            f"(сейчас сообщений: <b>{n}</b>).\n\n"
            "Контекст ИИ-агента начнётся с нуля. Заказы, лицензии и админ-права "
            "не затрагиваются.\n\n"
            "<i>Подтвердите кнопкой ниже.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    async def _cb_clear_my_chat_confirm(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if callback.from_user is None or not callback.data:
            await callback.answer()
            return
        parts = callback.data.split(":")
        if len(parts) != 3 or parts[0] != _ADMIN_CLEAR_CB:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        action, uid_s = parts[1], parts[2]
        try:
            target_uid = int(uid_s)
        except ValueError:
            await callback.answer("Ошибка", show_alert=True)
            return
        if callback.from_user.id != target_uid:
            await callback.answer("Это подтверждение не для вас", show_alert=True)
            return
        if not await is_telegram_admin(self.user_storage, callback.from_user.id):
            if not self._is_super_admin_user_id(callback.from_user.id):
                await callback.answer("Нет доступа", show_alert=True)
                return

        if action == "no":
            if callback.message:
                await callback.message.edit_text(
                    "❌ Удаление истории отменено.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            await callback.answer()
            return

        if action != "yes":
            await callback.answer()
            return

        stats = await self.user_storage.clear_private_chat_history(target_uid)
        await self.user_storage.clear_agent_session_id(target_uid)
        await state.clear()

        if callback.message:
            await callback.message.edit_text(
                "<b>✅ История личного чата очищена</b>\n\n"
                f"• messages (soft delete): {stats.get('messages', 0)}\n"
                f"• conversation_history: {stats.get('conversation_history', 0)}\n"
                "• сессия агента сброшена",
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        await callback.answer("Готово")
        logger.info(
            "[%s] Admin %s cleared private chat history: %s",
            self.name,
            target_uid,
            stats,
        )

    async def _cmd_churn_report(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        if message.from_user is None or self._bot is None or self.user_storage.pool is None:
            return

        uid = message.from_user.id
        busy = (
            "⏳ Собираю расширенную статистику по отвалу. "
            "Затем запрос к DeepSeek — может занять 1–3 минуты."
        )
        try:
            await self._bot.send_message(
                uid, busy, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except Exception as e:
            logger.warning("churn_report busy-msg: %s", e)

        try:
            churn = ClubChurnReportCollector(
                self.user_storage.pool,
                club_group_id=int(config.CLUB_GROUP_ID or 0),
            )
            payload = await churn.build_payload()
            html_body = churn.format_admin_html(payload)
            await self._send_html_in_dm_chunks(
                user_id=uid,
                html_text=html_body,
                header="<b>📉 Отчёт по отвалу</b>\n<i>Команда /churn</i>\n\n",
            )
        except Exception as e:
            logger.exception("churn_report build failed: %s", e)
            await self._dm_err(uid, f"Ошибка сборки отчёта по отвалу: {e}")
            return

        conclusion_html: Optional[str] = None
        key = (getattr(config, "DEEPSEEK_API_KEY", None) or "").strip()
        if key:
            about = load_aboutclub_text()
            json_blob = churn.payload_json_for_llm(payload)
            text = await analyze_churn_with_deepseek(
                api_key=key,
                about_club_text=about,
                churn_data_json=json_blob,
            )
            if text:
                safe = sanitize_telegram_html(text)
                conclusion_html = (
                    "<b>🤖 Заключение DeepSeek (по данным отчёта и aboutclub)</b>\n\n"
                    f"{safe}"
                )
            else:
                conclusion_html = (
                    "<b>🤖 Заключение DeepSeek</b>\n\n"
                    "Не удалось получить ответ API. Проверьте ключ "
                    "<code>DEEPSEEK_API_KEY</code> и лимиты."
                )
        else:
            conclusion_html = (
                "<b>🤖 Заключение DeepSeek</b>\n\n"
                "Ключ <code>DEEPSEEK_API_KEY</code> не задан — выведен только цифровой отчёт."
            )

        if conclusion_html:
            await self._send_html_in_dm_chunks(
                user_id=uid,
                html_text=conclusion_html,
                header="",
            )

    async def _cmd_graf(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        self._graf_metric_pick.pop(message.from_user.id, None)
        await message.reply(
            "📈 Выберите метрику:",
            reply_markup=self._graf_metric_kb(),
        )

    async def _cmd_td_conversion(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        await message.reply(
            "🚗 <b>Конверсия ТД</b>\n\n"
            "Выберите период (от первой покупки тест-драйва):",
            parse_mode=ParseMode.HTML,
            reply_markup=self._td_conv_period_kb(),
        )

    def _td_conv_period_kb(self) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        chunk: list[InlineKeyboardButton] = []
        for days in TD_REPORT_PERIOD_DAYS:
            chunk.append(
                InlineKeyboardButton(
                    text=f"{days} дн.",
                    callback_data=f"{_TD_CONV_PREFIX}:period:{days}",
                )
            )
            if len(chunk) == 3:
                rows.append(chunk)
                chunk = []
        if chunk:
            rows.append(chunk)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _td_conv_callback_handler(self, callback: CallbackQuery) -> None:
        if callback.message is None or callback.from_user is None:
            await callback.answer()
            return
        fake_message = callback.message.model_copy(update={"from_user": callback.from_user})
        if not await self._ensure_console_admin(fake_message, allow_private=True):
            await callback.answer("Нет доступа", show_alert=True)
            return
        data = callback.data or ""
        if not data.startswith(f"{_TD_CONV_PREFIX}:period:"):
            await callback.answer()
            return
        raw = data.rsplit(":", 1)[-1]
        try:
            days = int(raw)
        except ValueError:
            await callback.answer("Некорректный период", show_alert=True)
            return
        if days not in TD_REPORT_PERIOD_DAYS:
            await callback.answer("Некорректный период", show_alert=True)
            return
        if self.user_storage.pool is None:
            await callback.answer("БД недоступна", show_alert=True)
            return

        await callback.answer("Собираю отчёт…")
        uid = callback.from_user.id
        try:
            report = await collect_td_conversion_report(self.user_storage.pool, days)
            html_body = format_td_conversion_html(report)
            await self._send_html_in_dm_chunks(
                user_id=uid,
                html_text=html_body,
                header="<i>Команда /td</i>\n\n",
            )
        except Exception as e:
            logger.exception("[%s] td conversion report failed: %s", self.name, e)
            await self._dm_err(uid, f"Ошибка отчёта ТД: {e}")
            return

        try:
            await callback.message.reply("✅ Отчёт по ТД отправлен вам в личку.")
        except Exception:
            pass

    async def _cmd_excluded_payment(self, message: Message) -> None:
        if not await self._ensure_console_admin(message, allow_private=True):
            return
        await message.reply(
            "📉 <b>Отвалившиеся (просрочка)</b>\n\n"
            "Выберите период по дате окончания подписки:",
            parse_mode=ParseMode.HTML,
            reply_markup=self._excl_pay_period_kb(),
        )

    def _excl_pay_period_kb(self) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        chunk: list[InlineKeyboardButton] = []
        for days in EXCLUDED_REPORT_PERIOD_DAYS:
            chunk.append(
                InlineKeyboardButton(
                    text=f"{days} дн.",
                    callback_data=f"{_EXCL_PAY_PREFIX}:period:{days}",
                )
            )
            if len(chunk) == 3:
                rows.append(chunk)
                chunk = []
        if chunk:
            rows.append(chunk)
        rows.append(
            [
                InlineKeyboardButton(
                    text="Всё время",
                    callback_data=f"{_EXCL_PAY_PREFIX}:period:all",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _excl_pay_callback_handler(self, callback: CallbackQuery) -> None:
        if callback.message is None or callback.from_user is None:
            await callback.answer()
            return
        fake_message = callback.message.model_copy(update={"from_user": callback.from_user})
        if not await self._ensure_console_admin(fake_message, allow_private=True):
            await callback.answer("Нет доступа", show_alert=True)
            return
        data = callback.data or ""
        if not data.startswith(f"{_EXCL_PAY_PREFIX}:period:"):
            await callback.answer()
            return
        raw = data.rsplit(":", 1)[-1]
        period_days: Optional[int]
        if raw == "all":
            period_days = None
        else:
            try:
                period_days = int(raw)
            except ValueError:
                await callback.answer("Некорректный период", show_alert=True)
                return
            if period_days not in EXCLUDED_REPORT_PERIOD_DAYS:
                await callback.answer("Некорректный период", show_alert=True)
                return
        if self.user_storage.pool is None:
            await callback.answer("БД недоступна", show_alert=True)
            return

        await callback.answer("Собираю отчёт…")
        uid = callback.from_user.id
        try:
            report = await collect_excluded_payment_report(
                self.user_storage.pool, period_days
            )
            html_body = format_excluded_payment_html(report)
            await self._send_html_in_dm_chunks(
                user_id=uid,
                html_text=html_body,
                header="<i>Команда /excluded</i>\n\n",
            )
        except Exception as e:
            logger.exception("[%s] excluded payment report failed: %s", self.name, e)
            await self._dm_err(uid, f"Ошибка отчёта по исключённым: {e}")
            return

        try:
            await callback.message.reply("✅ Отчёт по исключённым отправлен вам в личку.")
        except Exception:
            pass

    def _graf_metric_kb(self) -> InlineKeyboardMarkup:
        def cb(metric: str) -> str:
            return f"{_GRAF_PREFIX}:metric:{metric}"

        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="💰 Сумма оплат (день)", callback_data=cb("total_amount")),
                    InlineKeyboardButton(text="✅ Оплаченные заказы", callback_data=cb("paid_orders")),
                ],
                [
                    InlineKeyboardButton(text="🕒 Неоплаченные заказы", callback_data=cb("pending_orders")),
                    InlineKeyboardButton(text="👥 Активные за вчера", callback_data=cb("active_users")),
                ],
                [
                    InlineKeyboardButton(text="🆕 Новые за вчера", callback_data=cb("new_users")),
                    InlineKeyboardButton(text="🧾 Всего пользователей", callback_data=cb("total_users")),
                ],
                [
                    InlineKeyboardButton(text="💳 Сумма оплат (месяц)", callback_data=cb("month_total_amount")),
                    InlineKeyboardButton(text="📅 Оплаты (месяц)", callback_data=cb("month_paid_orders")),
                ],
            ]
        )

    def _graf_period_kb(self) -> InlineKeyboardMarkup:
        def cb(days: int) -> str:
            return f"{_GRAF_PREFIX}:period:{days}"

        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="7 дней", callback_data=cb(7)),
                    InlineKeyboardButton(text="30 дней", callback_data=cb(30)),
                    InlineKeyboardButton(text="90 дней", callback_data=cb(90)),
                ],
                [
                    InlineKeyboardButton(text="180 дней", callback_data=cb(180)),
                    InlineKeyboardButton(text="365 дней", callback_data=cb(365)),
                ],
            ]
        )

    async def _graf_callback_handler(self, callback: CallbackQuery) -> None:
        if callback.message is None or callback.from_user is None:
            await callback.answer()
            return
        fake_message = callback.message.model_copy(update={"from_user": callback.from_user})
        if not await self._ensure_console_admin(fake_message, allow_private=True):
            await callback.answer("Нет доступа", show_alert=True)
            return
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 3:
            await callback.answer()
            return
        _, stage, value = parts
        if stage == "metric":
            self._graf_metric_pick[callback.from_user.id] = value
            await callback.message.edit_text(
                f"📈 Метрика: <b>{html_mod.escape(value)}</b>\nВыберите период:",
                parse_mode=ParseMode.HTML,
                reply_markup=self._graf_period_kb(),
            )
            await callback.answer()
            return
        if stage != "period":
            await callback.answer()
            return
        metric = self._graf_metric_pick.get(callback.from_user.id)
        if not metric:
            await callback.answer("Сначала выберите метрику", show_alert=True)
            return
        try:
            days = max(2, min(3650, int(value)))
        except ValueError:
            await callback.answer("Некорректный период", show_alert=True)
            return
        await callback.answer("Строю график…")

        metric_titles = {
            "total_users": ("Всего пользователей", "#2E86AB"),
            "active_users": ("Активные за вчера", "#E67E22"),
            "new_users": ("Новые за вчера", "#9B59B6"),
            "pending_orders": ("Неоплаченные за вчера", "#C0392B"),
            "paid_orders": ("Оплаченные за вчера", "#27AE60"),
            "total_amount": ("Сумма оплат за вчера (₽)", "#16A085"),
            "month_paid_orders": ("Оплаченные за месяц", "#2980B9"),
            "month_total_amount": ("Сумма оплат за месяц (₽)", "#8E44AD"),
            "active_licenses": ("Активные лицензии", "#D35400"),
            "users_expired": ("Просроченные лицензии", "#7F8C8D"),
        }
        if metric not in metric_titles:
            allowed = ", ".join(sorted(metric_titles.keys()))
            await callback.message.reply(
                f"❌ Неизвестная метрика <code>{html_mod.escape(metric)}</code>.\n"
                f"Доступно: <code>{html_mod.escape(allowed)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        rows = await self._fetch_report_snapshot_series(metric=metric, days=days)
        if len(rows) < 2:
            await callback.message.reply("ℹ️ Недостаточно точек для графика (нужно минимум 2 снепшота).")
            return

        title, color = metric_titles[metric]
        png = self._build_line_chart_png(rows, title=title, color=color)
        if png is None:
            await self._dm_err(callback.from_user.id, "Для /graf нужен matplotlib. Установите зависимость и перезапустите бота.")
            return

        caption = (
            f"📈 <b>{html_mod.escape(title)}</b>\n"
            f"Период: последние {days} дн., точек: {len(rows)}"
        )
        try:
            await self._bot.send_photo(
                chat_id=callback.from_user.id,
                photo=png,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning("graf send_photo failed: %s", e)
            await self._dm_err(callback.from_user.id, f"Не удалось отправить график: {e}")
            return
        await callback.message.reply("✅ График отправлен вам в личку.")

    @staticmethod
    def _trim_metrics_for_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
        """Убирает тяжёлые поля, чтобы metrics_json не раздувался в БД."""
        out = dict(metrics)
        comps = out.get("comparisons")
        if isinstance(comps, dict):
            snaps = comps.get("snapshots")
            if isinstance(snaps, dict):
                trimmed: dict[str, Any] = {}
                for k, v in snaps.items():
                    if not isinstance(v, dict):
                        trimmed[k] = v
                        continue
                    trimmed[k] = {
                        key: v[key]
                        for key in (
                            "snapshot_date",
                            "total_amount",
                            "active_licenses",
                            "paid_orders",
                        )
                        if key in v
                    }
                out["comparisons"] = {"snapshots": trimmed}
        llm = out.get("llm")
        if isinstance(llm, dict):
            out["llm"] = {
                k: (v[:2000] + "…" if isinstance(v, str) and len(v) > 2000 else v)
                for k, v in llm.items()
            }
        return out

    async def _save_report_snapshot(
        self,
        *,
        metrics: dict[str, Any],
        report_html: str,
        source: str,
    ) -> None:
        snapshot_date = (datetime.now() - timedelta(days=1)).date()
        pending = metrics.get("pending_orders") or {}
        paid = metrics.get("paid_orders") or {}
        month_paid = metrics.get("month_paid_orders") or {}
        async with self.user_storage.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO club_report_snapshots (
                    snapshot_date,
                    total_users, active_users, new_users,
                    pending_orders, pending_unique_users,
                    paid_orders, paid_unique_users, total_amount,
                    month_paid_orders, month_unique_users, month_total_amount,
                    active_licenses, users_expired,
                    report_html, metrics_json, source
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6,
                    $7, $8, $9,
                    $10, $11, $12,
                    $13, $14,
                    $15, $16::jsonb, $17
                )
                ON CONFLICT (snapshot_date) DO UPDATE SET
                    total_users = EXCLUDED.total_users,
                    active_users = EXCLUDED.active_users,
                    new_users = EXCLUDED.new_users,
                    pending_orders = EXCLUDED.pending_orders,
                    pending_unique_users = EXCLUDED.pending_unique_users,
                    paid_orders = EXCLUDED.paid_orders,
                    paid_unique_users = EXCLUDED.paid_unique_users,
                    total_amount = EXCLUDED.total_amount,
                    month_paid_orders = EXCLUDED.month_paid_orders,
                    month_unique_users = EXCLUDED.month_unique_users,
                    month_total_amount = EXCLUDED.month_total_amount,
                    active_licenses = EXCLUDED.active_licenses,
                    users_expired = EXCLUDED.users_expired,
                    report_html = EXCLUDED.report_html,
                    metrics_json = EXCLUDED.metrics_json,
                    source = EXCLUDED.source,
                    updated_at = NOW()
                """,
                snapshot_date,
                int(metrics.get("total_users", 0) or 0),
                int(metrics.get("active_users", 0) or 0),
                int(metrics.get("new_users", 0) or 0),
                int(pending.get("count", 0) or 0),
                int(pending.get("unique_users", 0) or 0),
                int(paid.get("count", 0) or 0),
                int(paid.get("unique_users", 0) or 0),
                float(paid.get("total_amount", 0) or 0),
                int(month_paid.get("count", 0) or 0),
                int(month_paid.get("unique_users", 0) or 0),
                float(metrics.get("month_total_amount", 0) or 0),
                int(metrics.get("active_licenses", 0) or 0),
                int(metrics.get("users_expired", 0) or 0),
                report_html,
                __import__("json").dumps(
                    self._trim_metrics_for_snapshot(metrics),
                    ensure_ascii=False,
                    default=str,
                ),
                source,
            )

    async def _fetch_report_snapshot_series(
        self,
        *,
        metric: str,
        days: int,
    ) -> list[tuple[date, float]]:
        query = f"""
            SELECT snapshot_date, {metric}::numeric AS v
            FROM club_report_snapshots
            WHERE snapshot_date >= CURRENT_DATE - $1::int
            ORDER BY snapshot_date
        """
        async with self.user_storage.get_connection() as conn:
            rows = await conn.fetch(query, days)
        out: list[tuple[date, float]] = []
        for r in rows:
            out.append((r["snapshot_date"], float(r["v"] or 0)))
        return out

    def _build_line_chart_png(
        self,
        rows: list[tuple[date, float]],
        *,
        title: str,
        color: str,
    ):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.dates as mdates
            import matplotlib.pyplot as plt
            from matplotlib.dates import DateFormatter
            from aiogram.types import BufferedInputFile
        except Exception:
            return None

        dates = [datetime.combine(d, datetime.min.time()) for d, _ in rows]
        vals = [v for _, v in rows]
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(dates, vals, marker="o", linestyle="-", linewidth=2, markersize=4, color=color)
        ax.set_title(title, fontsize=15, pad=14)
        ax.set_xlabel("Дата")
        ax.set_ylabel("Значение")
        ax.grid(True, alpha=0.25)
        ax.xaxis.set_major_formatter(DateFormatter("%d.%m.%Y"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate(rotation=35)
        if len(vals) >= 2:
            diff = vals[-1] - vals[0]
            pct = (diff / vals[0] * 100.0) if vals[0] else 0.0
            ax.text(
                0.02,
                0.97,
                f"Изменение: {diff:+,.2f} ({pct:+.1f}%)",
                transform=ax.transAxes,
                va="top",
                fontsize=10,
                bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.7},
            )
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=110)
        plt.close(fig)
        buf.seek(0)
        return BufferedInputFile(buf.getvalue(), filename="club_report_chart.png")

    async def _send_html_in_dm_chunks(self, *, user_id: int, html_text: str, header: str = "") -> None:
        payload = sanitize_telegram_html((header or "") + (html_text or ""))
        for chunk in split_telegram_html_message_chunks(payload, max_len=3800):
            await self._bot.send_message(
                chat_id=user_id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )


    def _extract_ticket_number(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = _TICKET_PATTERN.search(text)
        if not m:
            return None
        return m.group(0).lstrip("#").upper()

    async def _support_ticket_reply_error(
        self, ticket_number: str
    ) -> str:
        """Понятная ошибка, если apply_support_ticket_admin_reply не обновил строку."""
        ticket_number = (ticket_number or "").strip().upper()
        row = await self.user_storage.get_ticket_by_number(ticket_number)
        if not row:
            return f"Тикет {ticket_number} не найден в базе."
        status = str(row.get("status") or "")
        if status == "closed":
            updated = row.get("updated_at")
            when = (
                updated.strftime("%d.%m.%Y %H:%M")
                if hasattr(updated, "strftime")
                else "—"
            )
            return (
                f"Тикет {ticket_number} уже закрыт ({when}). "
                "Ответ был записан и отправлен пользователю — повторный ответ в этот тикет невозможен."
            )
        if status == "answered":
            return (
                f"Тикет {ticket_number}: ответ уже записан, доставка пользователю в очереди. "
                "Подождите или проверьте личку пользователя."
            )
        return f"Тикет {ticket_number} в статусе «{status}» — ответ сейчас принять нельзя."

    def _extract_target_user_id(self, text: str) -> Optional[int]:
        if not text:
            return None
        patterns = (
            r"🆔 User ID:\s*`?(\d+)`?",
            r"User ID:\s*`?(\d+)`?",
            r"ID:\s*`?(\d+)`?",
        )
        for p in patterns:
            m = re.search(p, text)
            if m:
                return int(m.group(1))
        return None

    async def _support_thread_reply(self, message: Message) -> None:
        if not self._bot:
            return
        if not await self._ensure_console_admin(message):
            return

        processing = await message.reply("⏳ Записываю ответ в тикет…")
        ticket_number: Optional[str] = None
        err: Optional[str] = None

        try:
            orig = message.reply_to_message
            orig_text = orig.text or orig.caption or ""
            ticket_number = self._extract_ticket_number(orig_text)
            if not ticket_number:
                err = "Не найден номер тикета (ожидается TKT_CL… / TKT_BB…) в сообщении."
                return

            body = (message.text or "").strip()
            if not body:
                err = "Пустой ответ."
                return

            row = await self.user_storage.apply_support_ticket_admin_reply(
                ticket_number,
                body,
                message.from_user.id,
            )
            if not row:
                err = await self._support_ticket_reply_error(ticket_number)
                return

            user_dm = format_user_support_ticket_reply_html(
                ticket_number=ticket_number,
                admin_response=body,
            )
            try:
                await self._bot.send_message(
                    int(row["user_id"]),
                    user_dm,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as send_exc:
                logger.warning(
                    "support_thread_reply immediate send failed ticket=%s user=%s: %s",
                    ticket_number,
                    row.get("user_id"),
                    send_exc,
                )
                ok_send = False
            else:
                ok_send = True

            await self.user_storage.update_ticket_status(
                ticket_number,
                "closed" if ok_send else "answered",
                admin_id=message.from_user.id,
                admin_response=body,
            )

            await self._append_support_answer_block(orig, body, message.from_user)
            if ok_send:
                await self._dm_ok(
                    message.from_user.id,
                    "✅ Ответ по тикету отправлен пользователю; тикет закрыт.",
                )
            else:
                await self._dm_ok(
                    message.from_user.id,
                    "⚠️ Ответ записан, но отправка пользователю не удалась — "
                    "<b>доставка включит фоновый цикл поддержки.</b>",
                )
        except Exception as e:
            logger.exception("support_thread_reply: %s", e)
            err = str(e)[:200]
        finally:
            for mid in (processing.message_id, message.message_id):
                try:
                    await self._bot.delete_message(message.chat.id, mid)
                except Exception:
                    pass
            if err:
                await self._dm_err(message.from_user.id, err, ticket_number)

    async def _append_support_answer_block(
        self,
        original: Message,
        reply_text: str,
        admin: User,
    ) -> None:
        raw = original.text or original.caption or ""
        base = re.sub(r"#\w+", "", raw).strip()
        while "\n\n\n" in base:
            base = base.replace("\n\n\n", "\n\n")
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        un = f" (@{admin.username})" if admin.username else ""
        block = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Ответ поддержки</b>\n"
            f"👤 <b>Админ:</b> {html_mod.escape(admin.full_name)}{html_mod.escape(un)}\n"
            f"⏰ <b>Время:</b> {ts}\n\n"
            f"{html_mod.escape(reply_text)}"
        )
        new_text = base + block
        await self._bot.edit_message_text(
            chat_id=original.chat.id,
            message_id=original.message_id,
            text=new_text,
            parse_mode=ParseMode.HTML,
        )

    async def _sales_thread_reply(self, message: Message) -> None:
        if not self._bot:
            return
        if not await self._ensure_console_admin(message):
            return

        processing = await message.reply("⏳ Записываю ответ…")
        target_uid: Optional[int] = None
        err: Optional[str] = None

        try:
            orig = message.reply_to_message
            orig_text = orig.text or orig.caption or ""
            target_uid = self._extract_target_user_id(orig_text)
            if not target_uid:
                err = "Не удалось извлечь User ID из сообщения."
                return

            body = (message.text or "").strip()
            if not body:
                err = "Пустой ответ."
                return

            reply_html = sanitize_telegram_html(body) + "\n\n"
            try:
                await self._bot.send_message(
                    target_uid,
                    f"💬 <b>Сообщение от отдела клуба</b>\n\n{reply_html}",
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as send_exc:
                logger.warning("sales_thread_reply send user=%s: %s", target_uid, send_exc)
                err = f"Не удалось отправить в личку: {send_exc}"[:200]
                return

            await self.user_storage.create_admin_response_delivered(
                target_uid,
                body,
                message.from_user.id,
            )

            await self._append_sales_answer_block(orig, body, message.from_user, target_uid)
            await self._dm_ok(
                message.from_user.id,
                f"✅ Отправлено пользователю <code>{target_uid}</code>.",
            )
        except Exception as e:
            logger.exception("sales_thread_reply: %s", e)
            err = str(e)[:200]
        finally:
            for mid in (processing.message_id, message.message_id):
                try:
                    await self._bot.delete_message(message.chat.id, mid)
                except Exception:
                    pass
            if err:
                await self._dm_err(message.from_user.id, err, label=str(target_uid) if target_uid else None)

    async def _append_sales_answer_block(
        self,
        original: Message,
        reply_text: str,
        admin: User,
        user_id: int,
    ) -> None:
        raw = original.text or original.caption or ""
        new_text = (
            raw.replace("#Новое", "")
            .replace("#новый", "")
            .replace("#Новый", "")
            .strip()
        )
        if "#закрыт" not in new_text.lower():
            new_text = (new_text + "\n\n#закрыт").strip() if new_text else "#закрыт"
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        un = f" (@{admin.username})" if admin.username else ""
        block = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Ответ отдела продаж</b>\n"
            f"👤 <b>Менеджер:</b> {html_mod.escape(admin.full_name)}{html_mod.escape(un)}\n"
            f"⏰ <b>Время:</b> {ts}\n\n"
            f"{html_mod.escape(reply_text)}"
        )
        new_text += block
        await self._bot.edit_message_text(
            chat_id=original.chat.id,
            message_id=original.message_id,
            text=new_text,
            parse_mode=ParseMode.HTML,
        )

    async def _forum_topic_reply(self, message: Message) -> None:
        """Ответ админа в персональном форум-топике → пересылка пользователю."""
        if not self._bot:
            return
        if not await self._ensure_console_admin(message):
            return

        topic_id = message.message_thread_id
        if not topic_id:
            return

        processing = await message.reply("⏳ Записываю ответ…")
        target_uid: Optional[int] = None
        err: Optional[str] = None

        try:
            body = (message.text or "").strip()
            if not body:
                err = "Пустой ответ."
                return

            # Определяем user_id: из маппинга топика или из текста оригинального сообщения
            target_uid = await self.user_storage.get_user_id_by_dialog_topic(topic_id)
            if not target_uid:
                orig = message.reply_to_message
                orig_text = (orig.text or orig.caption or "") if orig else ""
                target_uid = self._extract_target_user_id(orig_text)
            if not target_uid:
                err = (
                    "Не удалось определить пользователя: "
                    "топик не найден в маппинге и User ID отсутствует в исходном сообщении."
                )
                return

            reply_html = sanitize_telegram_html(body) + "\n\n"
            try:
                await self._bot.send_message(
                    target_uid,
                    f"💬 <b>Сообщение от отдела клуба</b>\n\n{reply_html}",
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as send_exc:
                logger.warning("forum_topic_reply send user=%s: %s", target_uid, send_exc)
                err = f"Не удалось отправить в личку: {send_exc}"[:200]
                return

            await self.user_storage.create_admin_response_delivered(
                target_uid,
                body,
                message.from_user.id,
            )

            # Подпись в топике
            ts = datetime.now().strftime("%d.%m.%Y %H:%M")
            un = f" (@{message.from_user.username})" if message.from_user.username else ""
            confirm = (
                f"✅ <b>Отправлено</b> пользователю <code>{target_uid}</code>\n"
                f"👤 {html_mod.escape(message.from_user.full_name)}{html_mod.escape(un)} • {ts}"
            )
            await self._bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=topic_id,
                text=confirm,
                parse_mode=ParseMode.HTML,
            )
            await self._dm_ok(
                message.from_user.id,
                f"✅ Отправлено пользователю <code>{target_uid}</code>.",
            )
        except Exception as e:
            logger.exception("forum_topic_reply: %s", e)
            err = str(e)[:200]
        finally:
            try:
                await self._bot.delete_message(message.chat.id, processing.message_id)
            except Exception:
                pass
            if err:
                await self._dm_err(
                    message.from_user.id, err,
                    label=str(target_uid) if target_uid else None,
                )

    async def _dm_ok(self, admin_id: int, text: str) -> None:
        try:
            await self._bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning("DM ok failed: %s", e)

    async def _dm_err(self, admin_id: int, err: str, label: Optional[str] = None) -> None:
        t = "⛔ <b>Ошибка</b>\n\n"
        if label:
            t += f"{html_mod.escape(label)}\n"
        t += html_mod.escape(err)
        try:
            await self._bot.send_message(admin_id, t, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning("DM err failed: %s", e)
