"""Анкета подарочной волны: FSM, deep links, /gift_campaign (АНК-1…9)."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.gift_application_eligibility import (
    check_gift_application_eligibility,
    count_remaining_tickets,
)
from bot.services.gift_application_score import score_gift_application
from bot.services.gift_application_screen import screen_gift_application
from bot.services.gift_application_select import (
    ensure_campaign_wave,
    finish_campaign_not_selected,
    select_applications_for_wave,
)
from bot.services.gift_wave_service import grant_wave_batch
from bot.states import GiftApplicationStates
from bot.texts import ru_gift_application as txt
from bot.utils.user_ui import with_main_menu
from config import config
from storage.db.gift_application import CAMPAIGN_ID

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def parse_gift_start_source(param: str) -> Optional[str]:
    p = (param or "").strip().lower()
    if p.startswith("gift_"):
        rest = p[5:]
        if rest in txt.GIFT_START_SOURCES:
            return rest
    return None


class GiftApplicationFeature(BaseFeature):
    name = "gift_application"

    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self._scheduler: Optional[AsyncIOScheduler] = None

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        admin_private = private
        gid = config.resolved_admin_group_id()
        admin_chat = F.chat.id == gid if gid else F.chat.id == -1

        dp.callback_query.register(
            self._cb_apply, F.data == txt.CB_APPLY, private
        )
        dp.callback_query.register(
            self._cb_start_form, F.data == txt.CB_START_FORM, private
        )
        dp.callback_query.register(
            self._cb_continue, F.data == txt.CB_CONTINUE, private
        )
        dp.callback_query.register(
            self._cb_cancel, F.data == txt.CB_CANCEL, private
        )
        dp.callback_query.register(
            self._cb_ready_yes, F.data == txt.CB_READY_YES, private
        )
        dp.callback_query.register(
            self._cb_ready_no, F.data == txt.CB_READY_NO, private
        )
        dp.callback_query.register(
            self._cb_rules, F.data == txt.CB_RULES, private
        )
        dp.callback_query.register(
            self._cb_review, F.data.startswith("gift_rev:"), private
        )
        dp.callback_query.register(
            self._cb_return_promo, F.data == "gift_return_promo", private
        )

        dp.message.register(
            self._cmd_gift_campaign, admin_private, Command("gift_campaign")
        )
        dp.message.register(
            self._cmd_gift_campaign, admin_chat, Command("gift_campaign")
        )
        dp.message.register(
            self._cmd_gift_revoke, admin_private, Command("gift_revoke")
        )
        dp.message.register(
            self._cmd_gift_revoke, admin_chat, Command("gift_revoke")
        )

    async def initialize(self) -> None:
        await super().initialize()
        if not getattr(config, "GIFT_CAMPAIGN_ENABLED", True):
            logger.info("[%s] disabled", self.name)
            return
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._scheduler.add_job(
            self._hourly_tick,
            IntervalTrigger(hours=1),
            id="gift_app_hourly",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info("[%s] scheduler started", self.name)

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    # ----- deep link / entry -----

    async def try_open_from_start(
        self, message: Message, state: FSMContext, param: str
    ) -> bool:
        source = parse_gift_start_source(param)
        if not source:
            return False
        await self.open_entry(message, state, source=source)
        return True

    async def open_entry(
        self,
        message: Message,
        state: FSMContext,
        *,
        source: str = "other",
        edit: bool = False,
    ) -> None:
        user_id = message.from_user.id if message.from_user else message.chat.id
        left = await count_remaining_tickets(self.user_storage)
        if left <= 0:
            await self._reply(message, txt.T6_SOLD_OUT_HTML, edit=edit)
            return

        elig = await check_gift_application_eligibility(self.user_storage, user_id)
        if not elig.eligible:
            await self._handle_ineligible(message, state, elig, source=source, edit=edit)
            return

        existing = await self.user_storage.get_gift_application(user_id)
        if existing and existing.get("status") not in (
            "draft",
            "cancelled",
            "ineligible",
            "expired",
        ):
            await self._reply(message, txt.T15_HTML, edit=edit)
            return

        app = await self.user_storage.upsert_gift_application_start(
            user_id, source=source, eligible=True
        )
        try:
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="gift_application",
                event_type="gift_app_opened",
                data={"source": source, "application_id": app.get("id")},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass

        kb = with_main_menu(
            [[InlineKeyboardButton(text=txt.BTN_APPLY_SHORT, callback_data=txt.CB_START_FORM)]]
        )
        await self._reply(
            message,
            txt.T6_HTML.format(left=left),
            reply_markup=kb,
            edit=edit,
        )

    async def _handle_ineligible(
        self, message: Message, state: FSMContext, elig, *, source: str, edit: bool
    ) -> None:
        user_id = message.from_user.id
        await self.user_storage.upsert_gift_application_start(
            user_id,
            source=source,
            eligible=False,
            ineligible_reason=elig.reason,
        )
        try:
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="gift_application",
                event_type="gift_app_ineligible",
                data={"reason": elig.reason, "kind": elig.kind},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass

        if elig.kind == "sold_out":
            await self._reply(message, txt.T17_HTML, edit=edit)
            return
        if elig.kind == "already_applied":
            await self._reply(message, txt.T15_HTML, edit=edit)
            return
        if elig.kind == "admin":
            return
        if elig.kind == "active_license":
            until = "—"
            lic = elig.active_license or {}
            exp = lic.get("expires_at")
            if exp:
                if getattr(exp, "tzinfo", None) is None:
                    exp = exp.replace(tzinfo=MSK)
                until = exp.astimezone(MSK).strftime("%d.%m.%Y")
            club = self.feature_manager.get("club_group") if self.feature_manager else None
            kb = None
            if club:
                url, kind = await club.get_group_link_for_user(user_id)
                if url and kind in ("post", "invite"):
                    label = (
                        "Открыть клуб"
                        if kind == "post"
                        else txt.BTN_OPEN_CLUB
                    )
                    kb = with_main_menu(
                        [[InlineKeyboardButton(text=label, url=url)]]
                    )
            await self._reply(
                message,
                txt.T19A_HTML.format(until=until),
                reply_markup=kb,
                edit=edit,
            )
            return
        if elig.kind == "expired_access":
            await self._offer_return_promo(message, user_id, edit=edit)
            return

    async def _offer_return_promo(
        self, message: Message, user_id: int, *, edit: bool = False
    ) -> None:
        guid = (getattr(config, "GIFT_RETURN_PROMO_GUID", None) or "").strip()
        if guid:
            try:
                await self.user_storage.assign_user_promo_campaign(user_id, guid)
                await self.user_storage.log_interaction(
                    user_id=user_id,
                    event_category="gift_application",
                    event_type="gift_return_offer",
                    data={"guid": guid},
                    source="gift_application",
                    outcome="success",
                )
            except Exception as e:
                logger.warning("return promo assign uid=%s: %s", user_id, e)
        kb = with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=txt.BTN_RETURN_DISCOUNT,
                        callback_data="gift_return_promo",
                    )
                ]
            ]
        )
        await self._reply(message, txt.T19B_HTML, reply_markup=kb, edit=edit)

    # ----- callbacks -----

    async def _cb_apply(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message:
            return
        await self.open_entry(callback.message, state, source="bot", edit=True)

    async def _cb_start_form(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        await self._ask_q1(callback.message, state, callback.from_user.id, edit=True)

    async def _cb_continue(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        app = await self.user_storage.get_gift_application(callback.from_user.id)
        if not app or app.get("status") != "draft":
            await self.open_entry(callback.message, state, source="bot", edit=True)
            return
        if not app.get("q1_about"):
            await self._ask_q1(callback.message, state, callback.from_user.id, edit=True)
        elif not app.get("q2_why"):
            await self._ask_q2(callback.message, state, edit=True)
        elif app.get("q3_ready") is None:
            await self._ask_q3(callback.message, state, edit=True)
        else:
            await self._ask_rules(callback.message, state, edit=True)

    async def _cb_cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.from_user or not callback.message:
            return
        app = await self.user_storage.get_gift_application(callback.from_user.id)
        if app and app.get("status") == "draft":
            await self.user_storage.cancel_gift_application(int(app["id"]))
        await state.clear()
        kb = with_main_menu(
            [[InlineKeyboardButton(text=txt.BTN_APPLY_SHORT, callback_data=txt.CB_APPLY)]]
        )
        await self._reply(callback.message, txt.T12_HTML, reply_markup=kb, edit=True)

    async def _cb_ready_yes(self, callback: CallbackQuery, state: FSMContext) -> None:
        await self._save_ready(callback, state, True)

    async def _cb_ready_no(self, callback: CallbackQuery, state: FSMContext) -> None:
        await self._save_ready(callback, state, False)

    async def _save_ready(
        self, callback: CallbackQuery, state: FSMContext, ready: bool
    ) -> None:
        await callback.answer()
        if not callback.from_user or not callback.message:
            return
        app = await self.user_storage.get_gift_application(callback.from_user.id)
        if not app or app.get("status") != "draft":
            return
        await self.user_storage.update_gift_application_answers(
            int(app["id"]), q3_ready=ready
        )
        try:
            await self.user_storage.log_interaction(
                user_id=callback.from_user.id,
                event_category="gift_application",
                event_type="gift_app_step",
                data={"step": 3, "ready": ready},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass
        await self._ask_rules(callback.message, state, edit=True)

    async def _cb_rules(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.from_user or not callback.message:
            return
        app = await self.user_storage.get_gift_application(callback.from_user.id)
        if not app or app.get("status") != "draft":
            return
        await self.user_storage.update_gift_application_answers(
            int(app["id"]), rules_accepted=True
        )
        submitted = await self.user_storage.submit_gift_application(int(app["id"]))
        await state.clear()
        if not submitted:
            await self._reply(
                callback.message,
                "Не удалось принять заявку — проверь, что все ответы заполнены.",
                edit=True,
            )
            return
        try:
            await self.user_storage.log_interaction(
                user_id=callback.from_user.id,
                event_category="gift_application",
                event_type="gift_app_submitted",
                data={"application_id": int(app["id"])},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass
        # q2 → stated_goals
        try:
            q2 = (submitted.get("q2_why") or "").strip()
            if q2:
                await self.user_storage.ensure_member_profile(callback.from_user.id)
                await self.user_storage.append_member_stated_goals_fragment(
                    callback.from_user.id, q2
                )
        except Exception as e:
            logger.debug("stated_goals from gift app: %s", e)

        await self._reply(callback.message, txt.T14_HTML, edit=True)
        asyncio.create_task(
            self._post_submit_pipeline(int(app["id"])),
            name=f"gift_screen_{app['id']}",
        )

    async def _cb_return_promo(self, callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        payment = self.feature_manager.get("payment") if self.feature_manager else None
        if payment and callback.message:
            try:
                await payment.show_tariffs(
                    callback, tariff_type="base", show_gift_button=False, state=state
                )
                return
            except Exception as e:
                logger.warning("return promo tariffs: %s", e)
        if callback.message:
            await self._reply(
                callback.message,
                "Открой меню подписки — скидка уже привязана к тебе.",
                edit=True,
            )

    async def _cb_review(self, callback: CallbackQuery) -> None:
        if not callback.from_user or not await is_telegram_admin(
            self.user_storage, callback.from_user.id
        ):
            await callback.answer("Нет доступа", show_alert=True)
            return
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) < 3:
            await callback.answer()
            return
        action, app_id_s = parts[1], parts[2]
        try:
            app_id = int(app_id_s)
        except ValueError:
            await callback.answer()
            return
        verdict = "pass" if action == "pass" else "reject"
        status = "screened" if verdict == "pass" else "rejected"
        await self.user_storage.set_gift_application_verdict(
            app_id,
            verdict=verdict,
            reason=f"admin:{callback.from_user.id}",
            reviewed_by=callback.from_user.id,
            status=status,
        )
        try:
            app = await self.user_storage.get_gift_application_by_id(app_id)
            if app:
                await self.user_storage.log_interaction(
                    user_id=int(app["user_id"]),
                    event_category="gift_application",
                    event_type="gift_app_reviewed",
                    data={"application_id": app_id, "verdict": verdict},
                    source="gift_application",
                    outcome="success",
                )
        except Exception:
            pass
        if verdict == "pass":
            await score_gift_application(
                user_storage=self.user_storage, application_id=app_id
            )
        await callback.answer("Готово")
        if callback.message:
            await callback.message.edit_text(
                (callback.message.text or "") + f"\n\n→ {verdict}",
                parse_mode=ParseMode.HTML,
            )

    # ----- FSM answers -----

    async def handle_message(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        current = await state.get_state()
        answer = txt.normalize_answer_text(text)
        is_voice = bool(message.voice) or (
            text or ""
        ).strip().lower().startswith("[голосовое")
        if current == GiftApplicationStates.waiting_q1.state:
            await self._on_q1(message, state, answer, is_voice=is_voice)
        elif current == GiftApplicationStates.waiting_q2.state:
            await self._on_q2(message, state, answer, is_voice=is_voice)

    async def _ask_q1(
        self, message: Message, state: FSMContext, user_id: int, *, edit: bool
    ) -> None:
        await state.set_state(GiftApplicationStates.waiting_q1)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=txt.BTN_CANCEL, callback_data=txt.CB_CANCEL)]
            ]
        )
        await self._reply(message, txt.T7_HTML, reply_markup=kb, edit=edit)

    async def _ask_q2(
        self, message: Message, state: FSMContext, *, edit: bool
    ) -> None:
        await state.set_state(GiftApplicationStates.waiting_q2)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=txt.BTN_CANCEL, callback_data=txt.CB_CANCEL)]
            ]
        )
        await self._reply(message, txt.T8_HTML, reply_markup=kb, edit=edit)

    async def _ask_q3(
        self, message: Message, state: FSMContext, *, edit: bool
    ) -> None:
        await state.set_state(GiftApplicationStates.waiting_q3)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=txt.BTN_READY_YES, callback_data=txt.CB_READY_YES)],
                [InlineKeyboardButton(text=txt.BTN_READY_LOOK, callback_data=txt.CB_READY_NO)],
            ]
        )
        await self._reply(message, txt.T9_HTML, reply_markup=kb, edit=edit)

    async def _ask_rules(
        self, message: Message, state: FSMContext, *, edit: bool
    ) -> None:
        await state.set_state(GiftApplicationStates.waiting_rules)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=txt.BTN_RULES_OK, callback_data=txt.CB_RULES)]
            ]
        )
        await self._reply(message, txt.T10_HTML, reply_markup=kb, edit=edit)

    async def _on_q1(
        self, message: Message, state: FSMContext, answer: str, *, is_voice: bool
    ) -> None:
        if len(answer) < 3:
            await message.answer(txt.T11_HTML)
            return
        app = await self.user_storage.get_gift_application(message.from_user.id)
        if not app or app.get("status") != "draft":
            await state.clear()
            return
        await self.user_storage.update_gift_application_answers(
            int(app["id"]), q1_about=answer[:2000]
        )
        try:
            await self.user_storage.log_interaction(
                user_id=message.from_user.id,
                event_category="gift_application",
                event_type="gift_app_step",
                data={"step": 1, "len": len(answer), "is_voice": is_voice},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass
        await self._ask_q2(message, state, edit=False)

    async def _on_q2(
        self, message: Message, state: FSMContext, answer: str, *, is_voice: bool
    ) -> None:
        if len(answer) < 3:
            await message.answer(txt.T11_HTML)
            return
        app = await self.user_storage.get_gift_application(message.from_user.id)
        if not app or app.get("status") != "draft":
            await state.clear()
            return
        await self.user_storage.update_gift_application_answers(
            int(app["id"]), q2_why=answer[:4000]
        )
        try:
            await self.user_storage.log_interaction(
                user_id=message.from_user.id,
                event_category="gift_application",
                event_type="gift_app_step",
                data={"step": 2, "len": len(answer), "is_voice": is_voice},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass
        await self._ask_q3(message, state, edit=False)

    async def _post_submit_pipeline(self, application_id: int) -> None:
        try:
            screened = await screen_gift_application(
                user_storage=self.user_storage,
                bot=self.bot,
                application_id=application_id,
            )
            if screened.get("verdict") == "pass":
                await score_gift_application(
                    user_storage=self.user_storage,
                    application_id=application_id,
                )
        except Exception as e:
            logger.error("post_submit pipeline #%s: %s", application_id, e, exc_info=True)

    # ----- admin -----

    async def _cmd_gift_campaign(
        self, message: Message, command: CommandObject
    ) -> None:
        if not await is_telegram_admin(self.user_storage, message.from_user.id):
            return
        args = (command.args or "").strip().split()
        sub = (args[0].lower() if args else "status")
        if sub in ("help", "?"):
            await self._send_help(message)
            return
        if sub in ("start",):
            await self.user_storage.get_or_create_gift_campaign_state()
            await self.user_storage.update_gift_campaign_state(started=True, stage=1)
            await message.answer("Кампания запущена (stage=1). Порции: /gift_campaign portion")
            return
        if sub == "pause":
            await self.user_storage.update_gift_campaign_state(mailing_paused=True, waves_paused=True)
            await message.answer("Пауза рассылки и волн.")
            return
        if sub == "resume":
            await self.user_storage.update_gift_campaign_state(
                mailing_paused=False, waves_paused=False
            )
            await message.answer("Снята пауза.")
            return
        if sub == "portion":
            from bot.services.gift_application_mailing import create_club_portion_draft

            cohort = args[1].upper() if len(args) > 1 else None
            force = "force" in [a.lower() for a in args[2:]]
            result = await create_club_portion_draft(
                user_storage=self.user_storage,
                bot=self.bot,
                admin_id=message.from_user.id,
                cohort=cohort,
                force=force,
            )
            if result.get("ok"):
                await message.answer(
                    "Черновик создан и отправлен тебе в личку "
                    f"(кампания <code>{result['campaign_id']}</code>, "
                    f"когорта <b>{result['cohort']}</b>, "
                    f"получателей <b>{result['added']}</b>).\n"
                    "Запуск — кнопкой под превью (стандартный воркер рассылок).",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await message.answer(f"Порция не создана: <code>{result}</code>", parse_mode=ParseMode.HTML)
            return
        if sub == "wave" and len(args) >= 2:
            try:
                idx = int(args[1])
            except ValueError:
                await message.answer("wave N")
                return
            wave = await ensure_campaign_wave(self.user_storage, wave_index=idx)
            if not wave:
                await message.answer("Не удалось создать волну")
                return
            sel = await select_applications_for_wave(
                self.user_storage,
                wave_id=int(wave["id"]),
                wave_index=idx,
            )
            await self.user_storage.set_gift_wave_status(int(wave["id"]), "running")
            grant = await grant_wave_batch(
                user_storage=self.user_storage,
                bot=self.bot,
                feature_manager=self.feature_manager,
                wave_id=int(wave["id"]),
            )
            await message.answer(f"Волна {idx}: select={sel} grant={grant}")
            return
        if sub == "finish":
            n = await finish_campaign_not_selected(self.user_storage)
            # Т17 оставшимся queued/not_selected/rejected
            await self._send_t17_remaining()
            await self.user_storage.update_gift_campaign_state(finished=True)
            await message.answer(f"Кампания завершена, not_selected={n}, Т17 разослан.")
            return
        if sub == "review":
            await self._send_review_queue(message)
            return
        await self._send_status(message)

    async def _send_status(self, message: Message) -> None:
        st = await self.user_storage.get_or_create_gift_campaign_state()
        left = await count_remaining_tickets(self.user_storage)
        stats = await self.user_storage.gift_application_stats()
        by_src = await self.user_storage.count_queued_by_source()
        mail = await self.user_storage.gift_mailing_stats()
        lines = [
            f"<b>Кампания {CAMPAIGN_ID}</b>",
            f"этап {st.get('stage')} из 3 · осталось билетов <b>{left}</b> из 150",
            f"mailing_paused={st.get('mailing_paused')} waves_paused={st.get('waves_paused')}",
            "",
            "<b>Заявки</b>: "
            + (", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "пусто"),
            "<b>Очередь по источникам</b>: "
            + (", ".join(f"{k}={v}" for k, v in sorted(by_src.items())) or "пусто"),
            "",
            "<b>Рассылка</b>:",
        ]
        if mail:
            for m in mail:
                lines.append(
                    f"  {m['cohort']}: sent={m['sent']} blocked={m['blocked']}"
                )
        else:
            lines.append("  ещё не отправлялась")
        lines.extend(
            [
                "",
                "Справка: <code>/gift_campaign help</code>",
            ]
        )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _send_help(self, message: Message) -> None:
        text = (
            "<b>Подарочная волна — /gift_campaign</b>\n\n"
            "<b>Когорты (portion)</b>\n"
            "• <code>TEST</code> — только админы (проверка текста и кнопки)\n"
            "• <code>K1</code> — писали в бот 2+ разных дня, активны за 60 дней, "
            "зарегистрированы 30+ дней назад\n"
            "• <code>K2</code> — 2+ дня в боте, но давно не писали "
            "(активность старше 60 дней), рег. 30+\n"
            "• <code>K3</code> — один день в боте или без сообщений, рег. 30+\n"
            "В любую порцию всегда добавляются админы. Черновик — стандартная "
            "<code>mailing_campaigns</code>, запуск кнопкой под превью.\n\n"
            "<b>Команды</b>\n"
            "• <code>/gift_campaign</code> — статус кампании\n"
            "• <code>/gift_campaign help</code> — эта справка\n"
            "• <code>/gift_campaign start</code> — отметить старт кампании (этап 1)\n"
            "• <code>/gift_campaign pause</code> — пауза рассылки и волн\n"
            "• <code>/gift_campaign resume</code> — снять паузу\n"
            "• <code>/gift_campaign portion [TEST|K1|K2|K3] [force]</code> — "
            "черновик Т1 в личку; <code>force</code> — игнорировать лимиты очереди\n"
            "• <code>/gift_campaign wave N</code> — отбор и выдача билетов волны 1–6\n"
            "• <code>/gift_campaign review</code> — очередь ручной проверки заявок\n"
            "• <code>/gift_campaign finish</code> — закрыть очередь и разослать Т17\n"
            "• <code>/gift_revoke USER_ID причина</code> — отозвать билет"
        )
        await message.answer(text, parse_mode=ParseMode.HTML)

    async def _send_review_queue(self, message: Message) -> None:
        rows = await self.user_storage.list_gift_applications_for_review(limit=10)
        if not rows:
            await message.answer("Очередь review пуста.")
            return
        for app in rows:
            q1 = html.escape((app.get("q1_about") or "")[:400])
            q2 = html.escape((app.get("q2_why") or "")[:600])
            body = (
                f"<b>#{app['id']}</b> uid={app['user_id']} src={app.get('source')}\n"
                f"Причина модели: {html.escape(app.get('verdict_reason') or '—')}\n\n"
                f"<b>О себе:</b> {q1}\n\n"
                f"<b>Что происходит:</b> {q2}"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Пропустить",
                            callback_data=f"{txt.CB_REVIEW_PASS}{app['id']}",
                        ),
                        InlineKeyboardButton(
                            text="Отказать",
                            callback_data=f"{txt.CB_REVIEW_REJECT}{app['id']}",
                        ),
                    ]
                ]
            )
            await message.answer(body, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _send_t17_remaining(self) -> None:
        async with self.user_storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id FROM gift_application
                WHERE campaign = $1
                  AND status IN ('queued', 'not_selected', 'rejected', 'care')
                """,
                CAMPAIGN_ID,
            )
        for r in rows:
            uid = int(r["user_id"])
            try:
                await self.bot.send_message(
                    uid, txt.T17_HTML, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            await asyncio.sleep(0.05)

    async def _cmd_gift_revoke(
        self, message: Message, command: CommandObject
    ) -> None:
        if not await is_telegram_admin(self.user_storage, message.from_user.id):
            return
        parts = (command.args or "").strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            await message.answer("/gift_revoke <user_id> <причина>")
            return
        uid = int(parts[0])
        reason = parts[1][:500]
        app = await self.user_storage.get_gift_application(uid)
        if app:
            await self.user_storage.set_gift_application_verdict(
                int(app["id"]),
                verdict="reject",
                reason=f"revoked:{reason}",
                reviewed_by=message.from_user.id,
                status="cancelled",
            )
        try:
            await self.user_storage.record_club_member_exclusion(
                uid, reason=reason, source="gift_rules"
            )
        except Exception:
            pass
        try:
            await self.user_storage.log_interaction(
                user_id=uid,
                event_category="gift_application",
                event_type="gift_app_revoked",
                data={"reason": reason},
                source="gift_application",
                outcome="success",
            )
        except Exception:
            pass
        await message.answer(f"Отозвано для {uid}")

    # ----- maintenance -----

    async def _hourly_tick(self) -> None:
        try:
            left = await count_remaining_tickets(self.user_storage)
            # напоминания
            drafts = await self.user_storage.list_draft_gift_applications_for_reminder(
                hours=24
            )
            for app in drafts:
                uid = int(app["user_id"])
                try:
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=txt.BTN_CONTINUE,
                                    callback_data=txt.CB_CONTINUE,
                                )
                            ]
                        ]
                    )
                    await self.bot.send_message(
                        uid,
                        txt.T13_HTML.format(left=left),
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                    )
                    await self.user_storage.set_gift_application_reminder(int(app["id"]))
                except Exception:
                    pass
                await asyncio.sleep(0.05)

            expired_drafts = await self.user_storage.expire_stale_gift_drafts(hours=48)
            for app in expired_drafts:
                try:
                    await self.user_storage.log_interaction(
                        user_id=int(app["user_id"]),
                        event_category="gift_application",
                        event_type="gift_app_abandoned",
                        data={"application_id": int(app["id"]), "step": "draft"},
                        source="gift_application",
                        outcome="success",
                    )
                except Exception:
                    pass

            expired_tickets = await self.user_storage.expire_unactivated_gift_tickets(
                days=7
            )
            for t in expired_tickets:
                try:
                    await self.user_storage.log_interaction(
                        user_id=int(t["user_id"]),
                        event_category="gift_application",
                        event_type="gift_ticket_expired",
                        data={"wave_id": int(t["wave_id"])},
                        source="gift_application",
                        outcome="success",
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error("[%s] hourly: %s", self.name, e, exc_info=True)

    async def _reply(
        self,
        message: Message,
        text: str,
        *,
        reply_markup=None,
        edit: bool = False,
    ) -> None:
        if edit and getattr(message, "message_id", None):
            try:
                await message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                pass
        await message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
