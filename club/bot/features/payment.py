"""
Фича оплаты и подписок.
Позволяет пользователям выбирать тарифы и оплачивать через ЮKassa или BZB.
"""

import logging
import json
import re
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from aiogram import Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.features.base import BaseFeature
from bot.services.promo_campaign_service import (
    apply_promo_to_tariffs,
    discount_percent_value,
    get_active_promo_for_user,
)
from bot.texts import media_file_ids as media_ids
from bot.texts import ru_payment as pay_txt
from bot.utils.telegram_identity import resolve_telegram_bot_username
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from config import config, russian_days_phrase
from bot.utils.user_ui import render_user_screen, with_main_menu
from bot.payments.payment_provider_router import (
    resolve_payment_service,
    resolve_payment_service_by_name,
    subscription_recurring_enabled_for_tariff,
)

logger = logging.getLogger(__name__)

# Колбэк для приложения оферты (должен начинаться с payment_ — см. фильтр register_handlers).
CALLBACK_PAYMENT_OFFER_PDF = "payment_offer_pdf"
# Временно: бот Насти — отключение автосписания (заглушка без БД).
CALLBACK_NASTYA_DISABLE_RECURRING = "payment_nastya_disable_recurring"

# Лимиты отправки PDF оферты (защита от спама и лишних запросов к Telegram).
OFFER_PDF_MIN_INTERVAL_SEC = 30
OFFER_PDF_MAX_PER_HOUR = 5

# Deep link: https://t.me/<bot>?start=promo_week — меню промо на 1 неделю (как кнопка из «Польза»).
START_PARAM_PROMO_WEEK = "promo_week"
# Суффикс payment_start_promo_* для той же ветки тарифов, что benefit1/benefit2.
_PROMO_WEEK_PAYMENT_SUFFIX = "promo_test1week_benefit1"


def resolve_promo_tariff_type_from_payment_start_suffix(promo_full: str) -> str:
    """Тип тарифа из хвоста ``payment_start_promo_<promo_full>`` (логика колбэка «Польза»)."""
    if "_" in promo_full:
        return promo_full.rsplit("_", 1)[0]
    return promo_full


def promo_week_channel_tag(param: str) -> Optional[str]:
    """
    Часть после ``promo_week_`` для разметки каналов (аналитика).
    ``promo_week`` без хвоста → None; ``promo_week_kos2203`` → ``kos2203``.
    """
    prefix = f"{START_PARAM_PROMO_WEEK}_"
    if param.startswith(prefix):
        return param[len(prefix) :] or None
    return None


def resolve_promo_tariff_type_from_start_param(param: str) -> Optional[str]:
    """
    Параметр ``/start`` → тип тарифа в БД.

    ``promo_week`` — короткая маркетинговая ссылка (то же, что кнопка промо в benefit).
    ``promo_week_<tag>`` — то же меню + тег (см. ``promo_week_channel_tag``) для разных ссылок по каналам.
    ``promo_test1week_benefit1`` — явный вариант с тем же разбором, что у inline-кнопки.
    ``promo_test1week`` — тип тарифа как есть (без отрезания суффикса).
    """
    if param == START_PARAM_PROMO_WEEK or promo_week_channel_tag(param) is not None:
        return resolve_promo_tariff_type_from_payment_start_suffix(_PROMO_WEEK_PAYMENT_SUFFIX)
    if param == _PROMO_WEEK_PAYMENT_SUFFIX or param in (
        "promo_test1week_benefit2",
        "promo_test1week_benefit3",
    ):
        return resolve_promo_tariff_type_from_payment_start_suffix(param)
    if param == "promo_test1week" or param.startswith("promo_test1week_"):
        return param
    return None


def promo_start_tariff_type_candidates(primary: str) -> list[str]:
    """
    По БД может лежать ``promo_test1week`` или ``promo_test1week_benefit`` —
    пробуем оба порядке (сначала вычисленный из deeplink тип).
    """
    alts = {
        "promo_test1week": ["promo_test1week_benefit"],
        "promo_test1week_benefit": ["promo_test1week"],
    }
    out = [primary]
    out.extend(alts.get(primary, []))
    return list(dict.fromkeys(out))


def build_promo_week_deeplink(bot_username: str, channel_tag: Optional[str] = None) -> str:
    """
    Ссылка для рассылок: открывает бота с меню промо-недели.

    :param channel_tag: опционально; попадает в ``start=promo_week_<tag>`` для разметки каналов
        (Telegram: ``A-Za-z0-9_-``, общая длина payload до 64 символов).
    """
    username = bot_username.lstrip("@")
    p = START_PARAM_PROMO_WEEK
    if channel_tag:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", channel_tag.strip())
        if safe:
            p = f"{START_PARAM_PROMO_WEEK}_{safe}"
    return f"https://t.me/{username}?start={p}"


def configured_offer_pdf_file_id() -> Optional[str]:
    """file_id PDF оферты: env ``PUBLIC_OFFER_PDF_FILE_ID`` или ``media_file_ids.py``."""
    fid = (config.PUBLIC_OFFER_PDF_FILE_ID or "").strip()
    if not fid:
        fid = (media_ids.PUBLIC_OFFER_PDF_FILE_ID or "").strip()
    return fid or None


class PaymentFeature(BaseFeature):
    """Фича оплаты и подписок"""
    
    @property
    def name(self) -> str:
        return "payment"
    
    async def initialize(self) -> None:
        """Инициализация фичи."""
        fid = configured_offer_pdf_file_id()
        if not fid:
            logger.info("[%s] PDF оферты не задан", self.name)
            return
        try:
            await self.bot.get_file(fid)
            self._offer_pdf_file_id = fid
            logger.info("[%s] PDF оферты подключён", self.name)
        except TelegramBadRequest as e:
            logger.warning(
                "[%s] PDF оферты: file_id недействителен для этого бота (%s). "
                "Загрузите PDF в бота и обновите PUBLIC_OFFER_PDF_FILE_ID / media_file_ids.py "
                "(команда /code_id).",
                self.name,
                e.message,
            )
        except Exception as e:
            logger.warning("[%s] PDF оферты: не удалось проверить file_id: %s", self.name, e)
    
    def __init__(
        self,
        user_storage,
        yookassa_service,
        bzb_service,
        bot,
        feature_manager=None,
        order_fulfillment=None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.yookassa = yookassa_service
        self.bzb_service = bzb_service
        self.bot = bot
        self.feature_manager = feature_manager
        self.order_fulfillment = order_fulfillment
        self._offer_pdf_file_id: Optional[str] = None
        self._offer_pdf_send_log: Dict[int, List[float]] = {}
    
    def _offer_pdf_available(self) -> bool:
        return bool(self._offer_pdf_file_id)

    def _offer_pdf_rate_limited(self, user_id: int) -> bool:
        now = time.monotonic()
        window_start = now - 3600
        recent = [t for t in self._offer_pdf_send_log.get(user_id, []) if t >= window_start]
        self._offer_pdf_send_log[user_id] = recent
        if len(recent) >= OFFER_PDF_MAX_PER_HOUR:
            return True
        if recent and (now - recent[-1]) < OFFER_PDF_MIN_INTERVAL_SEC:
            return True
        return False

    def _record_offer_pdf_send(self, user_id: int) -> None:
        self._offer_pdf_send_log.setdefault(user_id, []).append(time.monotonic())
    
    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики фичи."""
        dp.callback_query.register(
            self.handle_callback,
            F.data.startswith("payment_") & PRIVATE_INLINE_CALLBACK_ONLY,
        )
    
    async def get_bot_username(self) -> Optional[str]:
        """Username бота для return_url (get_me либо TELEGRAM_BOT_USERNAME в конфиге)."""
        return await resolve_telegram_bot_username(self.bot)
    
    async def show_tariffs(
        self,
        message_or_callback,
        is_gift: bool = False,
        tariff_type: str = 'base',
        show_gift_button: bool = True,
        *,
        state: FSMContext | None = None,
        skip_consent_check: bool = False,
    ):
        """
        Показывает пользователю список тарифов.
        Может принимать как Message, так и CallbackQuery.
        
        Параметры:
        - is_gift: режим подарка (меняет заголовок на "Выберите тариф для подарка")
        - tariff_type: тип тарифа для фильтрации ('base', 'promo_test1week', и т.д.)
        - show_gift_button: показывать кнопку "Подарить подписку" (по умолчанию True)
        
        Логика работы параметров:
        - Кнопка "Подарить подписку" показывается ТОЛЬКО если НЕ режим подарка (is_gift=False) 
        И если явно не запрещено (show_gift_button=True).
        Это сделано для обратной совместимости: старые вызовы без третьего параметра 
        работают как раньше (кнопка показывается), а новые могут её скрыть.
        """
        # Определяем тип и получаем user_id
        if hasattr(message_or_callback, 'message') and hasattr(message_or_callback, 'from_user'):
            # Это CallbackQuery
            user_id = message_or_callback.from_user.id
            target = message_or_callback.message
            is_callback = True
        else:
            # Это Message
            user_id = message_or_callback.from_user.id
            target = message_or_callback
            is_callback = False

        tariffs = await self.user_storage.get_active_tariffs(tariff_type=tariff_type)
        if not tariffs:
            if is_callback:
                await render_user_screen(
                    target, text=pay_txt.TARIFFS_UNAVAILABLE, edit=True
                )
                await message_or_callback.answer()
            else:
                await render_user_screen(
                    target, text=pay_txt.TARIFFS_UNAVAILABLE, edit=False
                )
            return

        promo = None
        if not is_gift and tariff_type == "base":
            promo = await get_active_promo_for_user(self.user_storage, user_id)
            if promo:
                tariffs = apply_promo_to_tariffs(tariffs, promo)

        # Формируем текст
        text_lines = []
        if is_gift:
            text_lines.append(pay_txt.TARIFFS_HEADER_GIFT)
        elif promo:
            pct = int(round(discount_percent_value(promo)))
            text_lines.append(
                pay_txt.tariffs_header_with_promo(
                    name=str(promo.get("name") or "акция"),
                    percent=pct,
                )
            )
        else:
            text_lines.append(pay_txt.TARIFFS_HEADER_SUBSCRIPTION)

        for tariff in tariffs:
            rub_price = next((p for p in tariff['prices'] if p['currency'] == 'RUB'), None)
            usd_price = next((p for p in tariff['prices'] if p['currency'] == 'USD'), None)
            if rub_price:
                current = int(rub_price['amount'])
                old = int(rub_price['old_amount']) if rub_price.get('old_amount') else None
                text_lines.append(
                    pay_txt.tariff_line_rub(name=tariff['name'], current=current, old=old)
                )
            if usd_price:
                current = int(usd_price['amount'])
                old = int(usd_price['old_amount']) if usd_price.get('old_amount') else None
                text_lines.append(
                    pay_txt.tariff_line_usd(name=tariff['name'], current=current, old=old)
                )

        text_lines.append(pay_txt.TARIFFS_FOOTER)
        text = "\n".join(text_lines)

        # Создаем клавиатуру
        keyboard_buttons = []
        for tariff in tariffs:
            if is_gift:
                callback_data = f"payment_select_gift_{tariff['id']}"
            else:
                callback_data = f"payment_select_{tariff['id']}"
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=pay_txt.tariff_select_button(name=tariff['name']),
                    callback_data=callback_data,
                )
            ])

        # 🔥 Кнопка "Подарить подписку" показывается ТОЛЬКО если НЕ режим подарка И разрешено кнопкой
        if not is_gift and show_gift_button:
            keyboard_buttons.append([
                InlineKeyboardButton(text=pay_txt.BTN_GIFT_SUBSCRIPTION, callback_data="payment_gift_start")
            ])

        if config.BOT_VARIANT == "nastya" and not is_gift:
            btn_label = getattr(pay_txt, "BTN_DISABLE_RECURRING", "Отключить автосписание")
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=btn_label,
                    callback_data=CALLBACK_NASTYA_DISABLE_RECURRING,
                )
            ])

        keyboard = with_main_menu(keyboard_buttons)

        if is_callback:
            try:
                await target.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"Can't edit message, sending new: {e}")
                await target.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            await message_or_callback.answer()
        else:
            await target.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
        logger.info(f"💰 Tariffs shown to user_id={user_id} (is_gift={is_gift}, show_gift_button={show_gift_button})")

    async def show_member_gift_tariffs(
        self,
        callback: CallbackQuery,
        state: FSMContext,
        recipient_user_id: int,
        recipient_name: str,
        *,
        recipient_anonymous: bool = False,
    ) -> None:
        """Тарифы для подарка продления участнику клуба."""
        from bot.texts import ru_member_gift as mg_txt

        fsm = await state.get_data()
        anonymous = recipient_anonymous or bool(fsm.get("gift_recipient_anonymous"))
        await state.update_data(
            is_member_gift=True,
            gift_recipient_user_id=recipient_user_id,
            gift_recipient_name=recipient_name,
            gift_recipient_anonymous=anonymous,
        )
        tariffs = await self.user_storage.get_active_tariffs(tariff_type="base")
        target = callback.message
        if not tariffs:
            await render_user_screen(
                target, text=pay_txt.TARIFFS_UNAVAILABLE, edit=True
            )
            await callback.answer()
            return

        if anonymous:
            header = mg_txt.TARIFFS_HEADER_ANON_HTML
        else:
            header = mg_txt.TARIFFS_HEADER_HTML.format(
                name=mg_txt.escape_name(recipient_name)
            )
        text_lines = [
            header,
            "",
        ]
        for tariff in tariffs:
            rub_price = next((p for p in tariff["prices"] if p["currency"] == "RUB"), None)
            usd_price = next((p for p in tariff["prices"] if p["currency"] == "USD"), None)
            if rub_price:
                current = int(rub_price["amount"])
                old = int(rub_price["old_amount"]) if rub_price.get("old_amount") else None
                text_lines.append(
                    pay_txt.tariff_line_rub(name=tariff["name"], current=current, old=old)
                )
            if usd_price:
                current = int(usd_price["amount"])
                old = int(usd_price["old_amount"]) if usd_price.get("old_amount") else None
                text_lines.append(
                    pay_txt.tariff_line_usd(name=tariff["name"], current=current, old=old)
                )
        text_lines.append(pay_txt.TARIFFS_FOOTER)
        text = "\n".join(text_lines)

        keyboard_buttons = [
            [
                InlineKeyboardButton(
                    text=pay_txt.tariff_select_button(name=tariff["name"]),
                    callback_data=f"payment_mgift_select_{tariff['id']}",
                )
            ]
            for tariff in tariffs
        ]
        keyboard = with_main_menu(keyboard_buttons)
        try:
            await target.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            await target.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer()

    async def start_angel_pool_checkout(
        self,
        callback: CallbackQuery,
        state: FSMContext,
        *,
        amount: float,
        currency_code: str,
        slots: int,
        tariff: Dict,
    ) -> None:
        """Создаёт заказ и ссылку на оплату ангельского взноса."""
        from bot.texts import ru_angel_pool as ap_txt

        user_id = callback.from_user.id
        currency_code = (currency_code or "RUB").strip().upper()
        tariff_id = int(tariff["id"])
        description = (
            f"Ангельский взнос: {slots} продл. — {tariff.get('name', 'клуб')}"
        )

        try:
            service, provider = resolve_payment_service(
                currency_code,
                yookassa_service=self.yookassa,
                bzb_service=self.bzb_service,
            )
        except (ValueError, RuntimeError) as e:
            logger.error(
                "Angel pool payment provider failed user=%s: %s", user_id, e
            )
            await render_user_screen(
                callback.message, text=pay_txt.PAYMENT_CREATE_FAILED, edit=True
            )
            await callback.answer()
            return

        order_id = await self.user_storage.create_order(
            user_id=user_id,
            tariff_id=tariff_id,
            currency=currency_code,
            amount=float(amount),
            is_gift=False,
            is_angel_pool=True,
            angel_pool_slots=int(slots),
        )

        followup = self.feature_manager.get("followup")
        if followup:
            await followup.on_order_created(user_id)

        if not order_id:
            await render_user_screen(
                callback.message, text=pay_txt.ORDER_CREATE_FAILED, edit=True
            )
            await callback.answer()
            return

        bot_username = await self.get_bot_username()
        if not bot_username:
            await render_user_screen(
                callback.message,
                text=pay_txt.BOT_USERNAME_ERROR_HTML,
                edit=True,
            )
            await callback.answer()
            return

        if provider == "yookassa":
            payment_url, payment_id, payment_method_id, metadata = await service.create_payment(
                amount=amount,
                description=description,
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
            )
        else:
            payment_url, payment_id, metadata = await service.create_payment(
                amount=amount,
                description=description,
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
                currency=currency_code,
                link_type="ONE_TIME",
            )
            payment_method_id = None

        if not payment_url or not payment_id:
            await render_user_screen(
                callback.message, text=pay_txt.PAYMENT_CREATE_FAILED, edit=True
            )
            await callback.answer()
            return

        user_telegram_data = json.dumps({
            "first_name": callback.from_user.first_name,
            "last_name": callback.from_user.last_name,
            "username": callback.from_user.username,
        })

        db_payment_id = await self.user_storage.create_payment(
            user_id=user_id,
            amount=float(amount),
            payment_type="angel_pool",
            provider=provider,
            provider_payment_id=payment_id,
            user_telegram_data=user_telegram_data,
            currency=currency_code,
            order_id=order_id,
            provider_checkout_url=payment_url,
        )

        await state.update_data(
            payment_id=db_payment_id,
            order_id=order_id,
            is_angel_pool=True,
            ap_slots=slots,
        )

        cur_label = ap_txt.currency_label(currency_code)
        text = ap_txt.CHECKOUT_HTML.format(
            slots=slots,
            slots_word=ap_txt.slots_word(slots),
            amount=ap_txt.format_amount(amount, currency_code),
            currency_label=cur_label,
        )

        kb_rows = [
            [
                InlineKeyboardButton(
                    text=pay_txt.BTN_GO_TO_PAYMENT,
                    url=payment_url,
                )
            ],
        ]
        fid = self._offer_pdf_file_id if self._offer_pdf_available() else None
        if fid:
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text=pay_txt.BTN_DOWNLOAD_OFFER_PDF,
                        callback_data=CALLBACK_PAYMENT_OFFER_PDF,
                    )
                ]
            )
        keyboard = with_main_menu(kb_rows)

        try:
            await callback.message.edit_text(
                text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
        except Exception:
            await callback.message.answer(
                text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
        await callback.answer()
        logger.info(
            "👼 Angel pool order=%s payment=%s user=%s slots=%s",
            order_id,
            db_payment_id,
            user_id,
            slots,
        )

    async def try_show_promo_tariffs_from_start(
        self, message: Message, param: str, *, state: FSMContext | None = None
    ) -> bool:
        """
        Показывает меню промо-тарифов по deep link ``/start <param>``.
        Возвращает True, если параметр распознан (в т.ч. когда тарифов нет в БД).
        """
        tariff_type = resolve_promo_tariff_type_from_start_param(param)
        if not tariff_type:
            return False

        user_id = message.from_user.id
        tariffs = []
        db_type_used = tariff_type
        for cand in promo_start_tariff_type_candidates(tariff_type):
            tariffs = await self.user_storage.get_active_tariffs(tariff_type=cand)
            if tariffs:
                db_type_used = cand
                break
        if not tariffs:
            await message.answer(pay_txt.PROMO_UNAVAILABLE)
            logger.warning(
                "Promo deep link: no tariffs tried=%s user_id=%s param=%r",
                promo_start_tariff_type_candidates(tariff_type),
                user_id,
                param,
            )
            return True

        await self.show_tariffs(
            message,
            is_gift=False,
            tariff_type=db_type_used,
            show_gift_button=False,
            state=state,
        )
        tag = promo_week_channel_tag(param)
        logger.info(
            "Promo deep link: tariffs shown user_id=%s param=%r resolved=%s db_type=%s channel_tag=%r",
            user_id,
            param,
            tariff_type,
            db_type_used,
            tag,
        )
        return True
    
    async def handle_callback(self, callback: CallbackQuery, state: FSMContext):
        """Обрабатывает callback'и от кнопок оплаты."""
        try:
            data = callback.data

            from bot.services.attribution_touch import parse_callback_data

            touch = parse_callback_data(data or "")
            if touch and callback.from_user:
                await self.user_storage.record_attribution_touch(
                    callback.from_user.id, touch, source_type="callback"
                )
                from bot.services.ref_key_registry import maybe_alert_new_marketing_touch

                await maybe_alert_new_marketing_touch(
                    self.user_storage, self.bot, touch
                )

            # ОБРАТНАЯ СОВМЕСТИМОСТЬ: старые callback'и от LicenseNotifier
            if data in ["license_buy", "license_renew"]:
                logger.info(f"🔄 Redirecting old license callback '{data}' to payment_start for user {callback.from_user.id}")
                await self.show_tariffs(callback, is_gift=False, state=state)
                await callback.answer()
                return

            # 🔥 ПРОМО-КОЛБЭКИ (payment_start_promo_...)
            if data.startswith("payment_start_promo_"):
                promo_full = data.replace("payment_start_", "")
                tariff_type = resolve_promo_tariff_type_from_payment_start_suffix(promo_full)

                logger.info(
                    "🎁 Promo callback: full=%s, tariff_type=%s for user %s",
                    promo_full,
                    tariff_type,
                    callback.from_user.id,
                )
                
                # Проверяем, существуют ли тарифы такого типа
                tariffs = await self.user_storage.get_active_tariffs(tariff_type=tariff_type)
                if not tariffs:
                    await callback.answer(pay_txt.PROMO_UNAVAILABLE_ALERT, show_alert=True)
                    return
                
                await self.show_tariffs(
                    callback,
                    is_gift=False,
                    tariff_type=tariff_type,
                    show_gift_button=False,
                    state=state,
                )
                await callback.answer()
                return

            # Подарок продления участнику клуба
            elif data.startswith("payment_mgift_select_"):
                tariff_id = int(data.replace("payment_mgift_select_", ""))
                await self._handle_tariff_selection(callback, state, tariff_id, is_gift=False)

            # СНАЧАЛА ОБРАБАТЫВАЕМ ПОДАРОЧНЫЕ ТАРИФЫ (содержат "gift_")
            elif data.startswith("payment_select_gift_"):
                tariff_str = data.replace("payment_select_gift_", "")
                # Безопасно парсим число (если есть лишние символы)
                match = re.search(r'\d+', tariff_str)
                if match:
                    tariff_id = int(match.group())
                else:
                    tariff_id = int(tariff_str)
                await self._handle_tariff_selection(callback, state, tariff_id, is_gift=True)
            
            # Промо-тарифы
            elif data.startswith("payment_select_promo_"):
                promo_key = data.replace("payment_select_promo_", "")
                await self._show_promo_tariffs(callback, state, promo_key)

            # 🔥 ПОТОМ ОБЫЧНЫЕ ТАРИФЫ
            elif data.startswith("payment_select_"):
                tariff_id = int(data.replace("payment_select_", ""))
                await self._handle_tariff_selection(callback, state, tariff_id, is_gift=False)
            
            # Выбор промо-тарифа
            elif data.startswith("payment_select_promo_tariff_"):
                tariff_id = int(data.replace("payment_select_promo_tariff_", ""))
                await self._handle_tariff_selection(callback, state, tariff_id, is_gift=False)

            # Кнопка "Оплатить" (из онбординга и маркированных CTA, напр. stuck_dialog_s1)
            elif data == "payment_start" or (
                data.startswith("payment_start_")
                and not data.startswith("payment_start_promo_")
            ):
                await self.show_tariffs(
                    callback, is_gift=False, tariff_type="base", state=state
                )
                await callback.answer()
            
            # Кнопка "Подарить подписку" - показываем инструкцию
            elif data == "payment_gift_start":
                await self._show_gift_info(callback, state)
                await callback.answer()
            
            # Кнопка "Продолжить" после инструкции о подарке
            elif data == "payment_gift_continue":
                await self.show_tariffs(callback, is_gift=True, state=state)
                await callback.answer()
            
            # Выбор валюты RUB
            elif data.startswith("payment_currency_rub_"):
                tariff_id = int(data.replace("payment_currency_rub_", ""))
                await self._handle_currency_selection(callback, state, 'rub', tariff_id)
            
            # Выбор валюты USD
            elif data.startswith("payment_currency_usd_"):
                tariff_id = int(data.replace("payment_currency_usd_", ""))
                await self._handle_currency_selection(callback, state, 'usd', tariff_id)
            
            # Назад к списку тарифов
            elif data == "payment_back_to_tariffs":
                await self._handle_back_to_tariffs(callback, state)
            
            # Проверка платежа (все ответы — внутри _handle_check_payment)
            elif data.startswith("payment_check_"):
                payment_id = int(data.replace("payment_check_", ""))
                await self._handle_check_payment(callback, payment_id)
                return

            elif data == CALLBACK_PAYMENT_OFFER_PDF:
                await self._handle_send_offer_pdf(callback)
                return

            elif data == CALLBACK_NASTYA_DISABLE_RECURRING:
                if config.BOT_VARIANT != "nastya":
                    await callback.answer(pay_txt.UNKNOWN_COMMAND_ALERT, show_alert=True)
                    return
                msg = getattr(pay_txt, "MSG_RECURRING_DISABLED", "Автосписание отключено")
                await callback.answer()
                if callback.message:
                    await callback.message.answer(msg)
                return

            # Отмена
            elif data == "payment_cancel":
                await self._handle_cancel(callback)
            
            # Неизвестный callback
            else:
                logger.warning(f"⚠️ Unknown payment callback: {data}")
                await callback.answer(pay_txt.UNKNOWN_COMMAND_ALERT, show_alert=True)
                return
            
            await callback.answer()
            
        except ValueError as e:
            logger.error(f"❌ ValueError in payment callback: {e}, data: {callback.data}")
            await callback.answer(pay_txt.DATA_ERROR_ALERT, show_alert=True)
        except Exception as e:
            logger.error(f"❌ Error in payment callback: {e}", exc_info=True)
            await callback.answer(pay_txt.GENERIC_ERROR_ALERT, show_alert=True)
    
    async def _show_gift_info(self, callback: CallbackQuery, state: FSMContext):
        """Показывает инструкцию о том, как работает подарок."""
        keyboard = with_main_menu([
            [InlineKeyboardButton(text=pay_txt.BTN_GIFT_CONTINUE, callback_data="payment_gift_continue")]
        ])
        
        gift_days_ru = russian_days_phrase(config.GIFT_LINK_VALIDITY_DAYS)
        text = pay_txt.gift_info_html(gift_days_ru=gift_days_ru)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()
    
    async def _show_currency_choice(self, callback: CallbackQuery, state: FSMContext, tariff: Dict):
        """Показывает выбор валюты для оплаты."""
        data = await state.get_data()
        is_gift = data.get("is_gift", False)
        user_id = callback.from_user.id
        display_tariff = tariff
        if not is_gift and (tariff.get("type") or "base") == "base":
            promo = await get_active_promo_for_user(self.user_storage, user_id)
            if promo:
                display_tariff = apply_promo_to_tariffs([tariff], promo)[0]
                await state.update_data(active_promo_guid=promo.get("campaign_guid"))

        text_lines = [pay_txt.currency_choice_header(tariff_name=display_tariff["name"])]
        keyboard_buttons = []

        for price in display_tariff["prices"]:
            currency = price['currency']
            amount = price['amount']
            old = price.get('old_amount')
            
            # Округляем до целого
            current_amount = int(amount)
            old_amount = int(old) if old else None
            
            if currency == 'RUB':
                text_line, button_text = pay_txt.currency_rub_text_and_button(
                    current=current_amount, old=old_amount
                )
                text_lines.append(text_line)
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"payment_currency_rub_{display_tariff['id']}"
                    )
                ])

            elif currency == 'USD':
                text_line, button_text = pay_txt.currency_usd_text_and_button(
                    current=current_amount, old=old_amount
                )
                text_lines.append(text_line)
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"payment_currency_usd_{display_tariff['id']}"
                    )
                ])
        
        text_lines.append(pay_txt.CURRENCY_CHOICE_FOOTER)
        text = "\n".join(text_lines)
        
        keyboard_buttons.append([InlineKeyboardButton(text=pay_txt.BTN_BACK, callback_data="payment_back_to_tariffs")])
        keyboard = with_main_menu(keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer()
    
    async def _handle_tariff_selection(self, callback: CallbackQuery, state: FSMContext, tariff_id: int, is_gift: bool = False):
        tariff = await self.user_storage.get_tariff_by_id(tariff_id)
        if not tariff:
            await render_user_screen(
                callback.message, text=pay_txt.TARIFF_NOT_FOUND, edit=True
            )
            return
        await state.update_data(selected_tariff=tariff, is_gift=is_gift)
        await self._show_currency_choice(callback, state, tariff)
    
    async def _handle_currency_selection(self, callback: CallbackQuery, state: FSMContext, currency: str, tariff_id: int):
        """Обрабатывает выбор валюты и создает заказ."""
        user_id = callback.from_user.id
        
        data = await state.get_data()
        tariff = data.get('selected_tariff')
        is_gift = data.get('is_gift', False)
        is_member_gift = bool(data.get('is_member_gift'))
        gift_recipient_user_id = data.get('gift_recipient_user_id')

        if not tariff:
            await render_user_screen(
                callback.message, text=pay_txt.TARIFF_NOT_FOUND, edit=True
            )
            return

        promo = None
        promo_guid = None
        if not is_gift and not is_member_gift and (tariff.get("type") or "base") == "base":
            promo = await get_active_promo_for_user(self.user_storage, user_id)
            if promo:
                promo_guid = str(promo.get("campaign_guid") or "")
                tariff = apply_promo_to_tariffs([tariff], promo)[0]
        
        # Находим цену для выбранной валюты
        price = next((p for p in tariff['prices'] if p['currency'].lower() == currency), None)
        if not price:
            await render_user_screen(
                callback.message, text=pay_txt.PRICE_NOT_FOUND, edit=True
            )
            return
        
        amount = price['amount']
        currency_code = 'RUB' if currency == 'rub' else 'USD'
        description = pay_txt.subscription_payment_description(
            tariff_name=tariff['name'], currency=currency
        )

        try:
            service, provider = resolve_payment_service(
                currency_code,
                yookassa_service=self.yookassa,
                bzb_service=self.bzb_service,
            )
        except (ValueError, RuntimeError) as e:
            logger.error(
                "Payment provider resolve failed user=%s currency=%s: %s",
                user_id,
                currency_code,
                e,
            )
            await render_user_screen(
                callback.message, text=pay_txt.PAYMENT_CREATE_FAILED, edit=True
            )
            return
        # 🔥 СОЗДАЕМ ЗАКАЗ
        order_id = await self.user_storage.create_order(
            user_id=user_id,
            tariff_id=tariff_id,
            currency=currency_code,
            amount=float(amount),
            is_gift=is_gift,
            promo_campaign_guid=promo_guid,
            gift_recipient_user_id=int(gift_recipient_user_id)
            if is_member_gift and gift_recipient_user_id
            else None,
        )

        # Запускаем дожим, если он есть
        followup = self.feature_manager.get("followup")
        if followup:
            await followup.on_order_created(user_id)

        if not order_id:
            await render_user_screen(
                callback.message, text=pay_txt.ORDER_CREATE_FAILED, edit=True
            )
            return
        
        bot_username = await self.get_bot_username()
        if not bot_username:
            await render_user_screen(
                callback.message,
                text=pay_txt.BOT_USERNAME_ERROR_HTML,
                edit=True,
            )
            return

        save_payment_method = (
            subscription_recurring_enabled_for_tariff(tariff)
            and provider == "yookassa"
        )

        # Создаем платеж
        if provider == "yookassa":
            payment_url, payment_id, payment_method_id, metadata = await service.create_payment(
                amount=amount,
                description=description,
                user_id=user_id,
                payment_type="subscription",
                bot_username=bot_username,
                save_payment_method=save_payment_method,
            )
        else:
            payment_url, payment_id, metadata = await service.create_payment(
                amount=amount,
                description=description,
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
                currency=currency_code,
                link_type="ONE_TIME",
            )
            payment_method_id = None
        
        if not payment_url or not payment_id:
            await render_user_screen(
                callback.message, text=pay_txt.PAYMENT_CREATE_FAILED, edit=True
            )
            return
        
        # Сохраняем данные пользователя
        user_telegram_data = json.dumps({
            'first_name': callback.from_user.first_name,
            'last_name': callback.from_user.last_name,
            'username': callback.from_user.username
        })
        
        # 🔥 СОЗДАЕМ ПЛАТЕЖ С ПРИВЯЗКОЙ К ЗАКАЗУ
        db_payment_id = await self.user_storage.create_payment(
            user_id=user_id,
            amount=float(amount),
            payment_type="subscription",
            provider=provider,
            provider_payment_id=payment_id,
            user_telegram_data=user_telegram_data,
            currency=currency_code,
            order_id=order_id,
            provider_checkout_url=payment_url,
        )
        
        await state.update_data(
            payment_id=db_payment_id,
            order_id=order_id,
            provider_payment_id=payment_id,
            tariff_id=tariff_id,
            amount=amount,  
            currency=currency_code,
            provider=provider
        )
        
        fid = self._offer_pdf_file_id if self._offer_pdf_available() else None
        text = pay_txt.checkout_message_html(
            tariff_name=tariff['name'],
            amount=amount,
            currency_code=currency_code,
            duration_days=tariff['duration_days'],
        )

        kb_rows = [
            [InlineKeyboardButton(
                text=pay_txt.BTN_GO_TO_PAYMENT,
                url=payment_url,
            )],
        ]
        if fid:
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text=pay_txt.BTN_DOWNLOAD_OFFER_PDF,
                        callback_data=CALLBACK_PAYMENT_OFFER_PDF,
                    )
                ]
            )
        keyboard = with_main_menu(kb_rows)

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        logger.info(f"💰 Order {order_id} created, payment {db_payment_id} for user {user_id}")

    async def _handle_send_offer_pdf(self, callback: CallbackQuery) -> None:
        """Отправляет PDF оферты в чат по Telegram file_id (как аудио в benefit)."""
        if not self._offer_pdf_available():
            await callback.answer(pay_txt.OFFER_NOT_CONNECTED_ALERT, show_alert=True)
            return
        user_id = callback.from_user.id
        if self._offer_pdf_rate_limited(user_id):
            await callback.answer(pay_txt.OFFER_RATE_LIMIT_ALERT, show_alert=True)
            return
        try:
            await callback.message.answer_document(
                document=self._offer_pdf_file_id,
                caption=pay_txt.OFFER_PDF_CAPTION,
                parse_mode=ParseMode.HTML,
            )
            self._record_offer_pdf_send(user_id)
            await callback.answer()
        except TelegramBadRequest as e:
            if "wrong file identifier" in (e.message or "").lower():
                self._offer_pdf_file_id = None
                logger.warning(
                    "[%s] PDF оферты отключён: file_id недействителен для этого бота",
                    self.name,
                )
            else:
                logger.warning("[%s] Не удалось отправить PDF оферты: %s", self.name, e.message)
            await callback.answer(
                pay_txt.OFFER_SEND_FAILED_ALERT,
                show_alert=True,
            )
        except Exception as e:
            logger.warning("[%s] Не удалось отправить PDF оферты: %s", self.name, e)
            await callback.answer(
                pay_txt.OFFER_SEND_FAILED_ALERT,
                show_alert=True,
            )

    async def _handle_back_to_tariffs(self, callback: CallbackQuery, state: FSMContext):
        """Возвращает к выбору тарифов."""
        data = await state.get_data()
        if data.get("is_member_gift") and data.get("gift_recipient_user_id"):
            await self.show_member_gift_tariffs(
                callback,
                state,
                int(data["gift_recipient_user_id"]),
                str(data.get("gift_recipient_name") or ""),
            )
            return
        await self.show_tariffs(callback, is_gift=False, state=state)
        await callback.answer()
    
    async def _handle_check_payment(self, callback: CallbackQuery, payment_id: int):
        """Проверка статуса у провайдера и общая дорожка finalize + выдача (как PaymentChecker)."""
        payment = await self.user_storage.get_payment(payment_id)
        if not payment:
            await render_user_screen(
                callback.message, text=pay_txt.PAYMENT_NOT_FOUND, edit=True
            )
            await callback.answer()
            return

        fo = self.order_fulfillment
        if not fo:
            await callback.answer(pay_txt.FULFILLMENT_NOT_CONFIGURED_ALERT, show_alert=True)
            return

        provider = payment.get("payment_provider", "yookassa")
        try:
            service, _ = resolve_payment_service_by_name(
                provider,
                yookassa_service=self.yookassa,
                bzb_service=self.bzb_service,
            )
        except RuntimeError:
            alert = (
                pay_txt.INTERNATIONAL_PAYMENT_UNAVAILABLE_ALERT
                if provider == "bzb"
                else pay_txt.PAYMENT_CREATE_FAILED
            )
            await callback.answer(alert, show_alert=True)
            return

        status, _details = await service.check_payment_status(
            payment["provider_payment_id"]
        )

        if status != "succeeded":
            if status == "pending":
                await callback.answer(
                    pay_txt.PAYMENT_PENDING_ALERT,
                    show_alert=True,
                )
            else:
                await callback.answer(
                    pay_txt.PAYMENT_NOT_FOUND_OR_CANCELLED_ALERT,
                    show_alert=True,
                )
            return

        order = await self.user_storage.get_order(payment["order_id"])
        if not order:
            await callback.answer(pay_txt.ORDER_NOT_FOUND_ALERT, show_alert=True)
            return

        rub_amount = await fo.compute_rub_amount(order, payment)
        if not rub_amount:
            await callback.answer(pay_txt.AMOUNT_RECALC_ERROR_ALERT, show_alert=True)
            return

        exchange_rate = rub_amount / float(order["amount"])
        await fo.finalize_pending_payment_or_none(
            payment_id=payment_id,
            provider_payment_id=payment["provider_payment_id"],
            rub_amount=rub_amount,
            exchange_rate=exchange_rate,
        )

        fresh = await self.user_storage.get_payment(payment_id)
        if not fresh or fresh.get("status") != "succeeded":
            await callback.answer(pay_txt.PAYMENT_RECORD_FAILED_ALERT, show_alert=True)
            return

        await fo.deliver_after_successful_payment_row(fresh)

        tariff = await self.user_storage.get_tariff_by_id(order["tariff_id"])
        lic = await self.user_storage.get_user_active_license(order["user_id"])
        tariff_name = (
            tariff["name"] if tariff else order.get("tariff_name") or pay_txt.DEFAULT_TARIFF_NAME
        )
        if order.get("is_gift"):
            await render_user_screen(
                callback.message,
                text=pay_txt.PAYMENT_SUCCESS_GIFT_HTML,
                edit=True,
            )
        elif lic:
            exp = lic["expires_at"].strftime("%d.%m.%Y")
            await render_user_screen(
                callback.message,
                text=pay_txt.payment_success_subscription_html(
                    tariff_name=tariff_name, exp=exp
                ),
                edit=True,
            )
        else:
            await render_user_screen(
                callback.message,
                text=pay_txt.PAYMENT_SUCCESS_GENERIC_HTML,
                edit=True,
            )

        await callback.answer()
        logger.info(f"✅ Payment {payment_id} confirmed manually by user")

    async def _handle_cancel(self, callback: CallbackQuery):
        """Отменяет процесс оплаты."""
        await render_user_screen(
            callback.message, text=pay_txt.PAYMENT_CANCELLED, edit=True
        )
    
    async def check_payment_background(self, payment_id: int):
        """Фоновая проверка: тот же путь finalize + fulfilment, что и в PaymentChecker."""
        try:
            payment = await self.user_storage.get_payment(payment_id)
            if not payment or payment["status"] != "pending":
                return
            fo = self.order_fulfillment
            if not fo:
                return

            provider = payment.get("payment_provider", "yookassa")
            try:
                service, _ = resolve_payment_service_by_name(
                    provider,
                    yookassa_service=self.yookassa,
                    bzb_service=self.bzb_service,
                )
            except RuntimeError:
                return

            status, _details = await service.check_payment_status(
                payment["provider_payment_id"]
            )
            if status != "succeeded":
                return

            order = await self.user_storage.get_order(payment["order_id"])
            if not order:
                return

            rub_amount = await fo.compute_rub_amount(order, payment)
            if not rub_amount:
                return
            exchange_rate = rub_amount / float(order["amount"])

            await fo.finalize_pending_payment_or_none(
                payment_id=payment_id,
                provider_payment_id=payment["provider_payment_id"],
                rub_amount=rub_amount,
                exchange_rate=exchange_rate,
            )

            fresh = await self.user_storage.get_payment(payment_id)
            if fresh and fresh.get("status") == "succeeded":
                await fo.deliver_after_successful_payment_row(fresh)

        except Exception as e:
            logger.error(f"❌ Error in background payment check: {e}")

    async def _show_promo_welcome(self, callback: CallbackQuery, promo_key: str):
        """Показывает приветственное сообщение для промо-тарифа и запускает выбор промо-тарифов"""
        
        # Разные тексты для разных промо
        if "test1week" in promo_key:
            welcome_text = pay_txt.PROMO_WELCOME_TEST1WEEK_HTML
        else:
            welcome_text = pay_txt.PROMO_WELCOME_GENERIC_HTML
        
        keyboard = with_main_menu([
            [InlineKeyboardButton(
                text=pay_txt.BTN_SELECT_PROMO_TARIFF,
                callback_data=f"payment_select_promo_{promo_key}",
            )]
        ])
        
        await callback.message.edit_text(
            welcome_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )        


    async def _show_promo_tariffs(self, callback: CallbackQuery, state: FSMContext, promo_key: str):
        """Показывает список промо-тарифов"""
        # Получаем промо-тарифы (тип = promo_test1week или аналогичный)
        tariff_type = f"promo_{promo_key}"
        tariffs = await self.user_storage.get_active_tariffs(tariff_type=tariff_type)
        
        if not tariffs:
            await render_user_screen(
                callback.message,
                text=pay_txt.PROMO_TARIFFS_UNAVAILABLE,
                edit=True,
            )
            logger.info(f"ищу промо-тарифы с типом {tariff_type}, но не нашлось")
            await callback.answer()
            return
        
        # Сохраняем promo_key в состоянии
        await state.update_data(is_promo=True, promo_key=promo_key)
        
        # Формируем текст
        text_lines = [pay_txt.PROMO_TARIFFS_HEADER]
        
        for tariff in tariffs:
            rub_price = next((p for p in tariff['prices'] if p['currency'] == 'RUB'), None)
            if rub_price:
                current = int(rub_price['amount'])
                old = int(rub_price['old_amount']) if rub_price.get('old_amount') else None
                text_lines.append(
                    pay_txt.tariff_line_rub(name=tariff['name'], current=current, old=old)
                )
        
        text_lines.append(pay_txt.TARIFFS_FOOTER)
        text = "\n".join(text_lines)
        
        # Создаем клавиатуру
        keyboard_buttons = []
        for tariff in tariffs:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=pay_txt.tariff_select_button(name=tariff['name']),
                    callback_data=f"payment_select_promo_tariff_{tariff['id']}"
                )
            ])
        
        keyboard_buttons.append([InlineKeyboardButton(text=pay_txt.BTN_BACK, callback_data="payment_back_to_tariffs")])
        keyboard = with_main_menu(keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer()    