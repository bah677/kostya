"""
Разовая рассылка всех сообщений цепочки подписки в тестовые ЛС при старте бота.

Включается только при ``SUBSCRIPTION_CHAIN_TEST=1`` в окружении.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup

from bot.features.subscription_reminder import (
    SubscriptionReminderFeature,
    _personalize_html,
    green_payment_button,
)
from config import config

logger = logging.getLogger(__name__)


def _bonus_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [green_payment_button(SubscriptionReminderFeature.BONUS_CONFIG["button"])]
        ]
    )


def _remove_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [green_payment_button(SubscriptionReminderFeature.REMOVE_CONFIG["button"])]
        ]
    )


def _build_step_list(
    feature: SubscriptionReminderFeature,
) -> List[Tuple[str, str, Optional[InlineKeyboardMarkup], str]]:
    """
    (пояснение для человека, шаблон текста с {имя}, клавиатура, slug для лога)
    """
    steps: List[Tuple[str, str, Optional[InlineKeyboardMarkup], str]] = []

    ordered = sorted(
        SubscriptionReminderFeature.REMINDER_CONFIG,
        key=lambda x: (-x["days_before"], x["order"]),
    )

    conditions = {
        7: (
            "Боевое условие: ежедневно 9:00 МСК, cron напоминаний. Активная подписка "
            "``license_type = subscription``, "
            "календарная дата окончания в Europe/Moscow ровно «сегодня по МСК + 7 дней». "
            "Идемпотентность: ``expiry_minus_7d_ord1`` + день отправки."
        ),
        5: (
            "Боевое условие: то же, но до окончания осталось 5 дней (дата expiry в МСК = сегодня + 5). "
            "Слуг ``expiry_minus_5d_ord1``. Две кнопки: реферальная ссылка и оплата."
        ),
        3: (
            "Боевое условие: до окончания 3 дня (дата expiry в МСК = сегодня + 3). "
            "Слуг ``expiry_minus_3d_ord1``."
        ),
        1: (
            "Боевое условие: до окончания 1 день (дата expiry в МСК = сегодня + 1). "
            "Слуг ``expiry_minus_1d_ord1``."
        ),
    }

    for rem in ordered:
        db = rem["days_before"]
        cond = conditions.get(
            db,
            f"Напоминание за {db} дней (см. subscription_reminder).",
        )
        slug = f"preview_reminder_{db}d"
        kb = feature._keyboard_reminder(rem["keyboard"])
        steps.append((cond, rem["text"], kb, slug))

    steps.append(
        (
            "Боевое условие: день 0 после окончания месяца подписки. "
            "Вчера по МСК дата окончания ``subscription`` совпала с «вчера»; "
            "джоб конвертирует лицензию в ``bonus_extension`` (+1 день). "
            "Слуг ``bonus_extension_plus_one_day``."
            "\n\nСообщение отправляется в тот же прогон, когда выдан бонусный день.",
            SubscriptionReminderFeature.BONUS_CONFIG["message"],
            _bonus_kb(),
            "preview_bonus_day0",
        )
    )

    steps.append(
        (
            "Боевое условие: день +1 после подарочного дня. "
            "Вчера по МСК истёк срок активной ``bonus_extension``; пользователю уходит это сообщение, "
            "затем кик из клуба и ``license`` → expired. Слуг ``post_bonus_expiry_final``.",
            SubscriptionReminderFeature.REMOVE_CONFIG["message"],
            _remove_kb(),
            "preview_post_bonus",
        )
    )

    churn_conds: Dict[int, str] = {
        5: (
            "Боевое условие: после полного выхода, +5 календарных дней в МСК с даты окончания "
            "последнего периода (``license.status = expired``, дата ``expires_at`` в МСК = сегодня − 5). "
            "Нет активной лицензии. Слуг ``churn_plus_5d``."
        ),
        10: (
            "Боевое условие: то же, якорь «сегодня − 10 дней» в МСК. Слуг ``churn_plus_10d``."
        ),
        18: (
            "Боевое условие: якорь «сегодня − 18 дней» в МСК. Слуг ``churn_plus_18d``. "
            "Шесть причин + кнопка возврата к оплате."
        ),
        30: (
            "Боевое условие: якорь «сегодня − 30 дней» в МСК. Слуг ``churn_plus_30d``."
        ),
    }

    for block in SubscriptionReminderFeature.CHURN_MESSAGES:
        d = block["days_after_exit"]
        cond = churn_conds.get(d, f"Churn +{d} дней после выхода.")
        kb = feature._keyboard_reminder(block["keyboard"])
        steps.append((cond, block["text"], kb, f"preview_churn_{d}d"))

    return steps


async def run_subscription_chain_preview(
    *,
    bot,
    user_storage,
    feature_manager,
    recipient_user_ids: Sequence[int],
    initial_delay_sec: float,
    between_steps_delay_sec: float,
) -> None:
    if not recipient_user_ids:
        logger.warning("subscription_chain_test: список user_id пуст — пропуск")
        return

    await asyncio.sleep(initial_delay_sec)

    try:
        referral = feature_manager.get("referral") if feature_manager else None
    except KeyError:
        referral = None

    helper = SubscriptionReminderFeature(
        user_storage=user_storage,
        bot=bot,
        feature_manager=feature_manager,
        referral_feature=referral,
    )

    steps = _build_step_list(helper)
    preview_no = len(steps)

    logger.info(
        "🧪 SUBSCRIPTION_CHAIN_TEST: старт %s сообщений → %s",
        preview_no,
        list(recipient_user_ids),
    )

    async def first_name(uid: int) -> Optional[str]:
        u = await user_storage.get_user(uid)
        return (u or {}).get("first_name")

    for idx, (condition, template, kb, slug) in enumerate(steps):
        for uid in recipient_user_ids:
            fn = await first_name(uid)
            body = _personalize_html(template, fn)
            cond_esc = html.escape(condition, quote=False)
            text = (
                "<b>🧪 Тест цепочки подписки</b>\n\n"
                f"<i>{cond_esc}</i>\n\n"
                "————————————\n\n"
                f"{body}"
            )
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=text,
                    reply_markup=kb,
                    parse_mode=ParseMode.HTML,
                )
                logger.info(
                    "🧪 preview sent step %s/%s slug=%s → uid=%s",
                    idx + 1,
                    preview_no,
                    slug,
                    uid,
                )
            except Exception as e:
                logger.error(
                    "🧪 preview failed slug=%s uid=%s: %s",
                    slug,
                    uid,
                    e,
                    exc_info=True,
                )
            await asyncio.sleep(0.35)

        if idx < len(steps) - 1:
            await asyncio.sleep(between_steps_delay_sec)

    logger.info("🧪 SUBSCRIPTION_CHAIN_TEST: последовательность завершена")


def start_subscription_chain_preview_task(app: Any) -> asyncio.Task:
    """Запускает корутину в фоне; вызывать после инициализации фич."""
    delay = float(config.SUBSCRIPTION_CHAIN_TEST_DELAY_SEC)
    uids = config.SUBSCRIPTION_CHAIN_TEST_USER_IDS

    async def _runner():
        try:
            await run_subscription_chain_preview(
                bot=app.bot,
                user_storage=app.user_storage,
                feature_manager=app.feature_manager,
                recipient_user_ids=uids,
                initial_delay_sec=delay,
                between_steps_delay_sec=delay,
            )
        except asyncio.CancelledError:
            logger.info("SUBSCRIPTION_CHAIN_TEST: задача отменена")
            raise
        except Exception as e:
            logger.error("SUBSCRIPTION_CHAIN_TEST: %s", e, exc_info=True)

    return asyncio.create_task(_runner())
