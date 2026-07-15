#!/usr/bin/env python3
"""
Разовая досылка уведомлений об ангельском взносе (если fulfillment упал на посте в группу).

Не повторяет раздачу продлений — только админ-чат и топик доски добрых дел в клубной группе.

Примеры (из dev, prod .env):

  cd /home/appuser/dev/club

  ./venv/bin/python scripts/send_angel_pool_replay_notifications.py --dry-run

  ./venv/bin/python scripts/send_angel_pool_replay_notifications.py --order-id 808
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_mod
import importlib
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot

from bot.texts import ru_angel_pool as ap_txt
from storage.user_storage import UserStorage

logger = logging.getLogger("send_angel_pool_replay")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"
DEFAULT_ORDER_ID = 808


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    import config as config_module

    importlib.reload(config_module)
    return config_module.load_config()


async def _fetch_payment_for_order(storage: UserStorage, order_id: int) -> Optional[Dict[str, Any]]:
    async with storage.get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM payments
            WHERE order_id = $1 AND status = 'succeeded'
            ORDER BY id DESC
            LIMIT 1
            """,
            order_id,
        )
    return dict(row) if row else None


async def _fetch_recipient_ids(storage: UserStorage, order_id: int) -> List[int]:
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT recipient_user_id
            FROM angel_pool_recipients
            WHERE order_id = $1
            ORDER BY id
            """,
            order_id,
        )
    return [int(r["recipient_user_id"]) for r in rows]


def _build_admin_html(
    *,
    order: Dict[str, Any],
    payment: Dict[str, Any],
    winner_ids: List[int],
    delivered: int,
) -> str:
    user_data = payment.get("user_telegram_data") or {}
    if isinstance(user_data, str):
        user_data = json.loads(user_data)
    fn = (user_data.get("first_name") or "").strip()
    ln = (user_data.get("last_name") or "").strip()
    full_name = f"{fn} {ln}".strip() or "Не указано"
    username_display = (
        "@" + user_data["username"] if user_data.get("username") else "нет username"
    )
    rub_amount = float(payment.get("amount_rub") or 0)
    requested_slots = int(order.get("angel_pool_slots") or 0)
    winners_str = ", ".join(str(uid) for uid in winner_ids) or "—"

    return (
        "👼 <b>АНГЕЛЬСКИЙ ВЗНОС</b> <i>(досылка уведомления)</i>\n\n"
        f"💰 <b>Сумма:</b> {order['amount']} {order['currency']}\n"
        f"💳 <b>В рублях:</b> {rub_amount:.2f} RUB\n"
        f"🎁 <b>Слотов по сумме:</b> {requested_slots}\n"
        f"✅ <b>Выдано продлений:</b> {delivered}\n"
        f"👼 <b>Ангел:</b> {html_mod.escape(full_name)}\n"
        f"🆔 <b>User ID:</b> <code>{order['user_id']}</code>\n"
        f"📱 <b>Username:</b> {html_mod.escape(username_display)}\n"
        f"🎯 <b>Получатели (user_id):</b> {winners_str}\n"
        f"📦 <b>Заказ:</b> <code>{order['id']}</code> · платёж <code>{payment['id']}</code>"
    )


async def run(args: argparse.Namespace) -> int:
    cfg = _load_config(args.env_file)
    from bot.services import wish_board_notify as wb_notify
    from bot.utils.admin_channel import send_admin_html_message

    if not cfg.MIRON_BOT_TOKEN:
        logger.error("MIRON_BOT_TOKEN не задан в %s", args.env_file)
        return 1

    storage = UserStorage(cfg.database_url)
    bot = Bot(token=cfg.MIRON_BOT_TOKEN)

    try:
        order_id = int(args.order_id)
        order = await storage.get_order(order_id)
        if not order:
            logger.error("Заказ %s не найден", order_id)
            return 1
        if not order.get("is_angel_pool"):
            logger.error("Заказ %s не ангельский взнос", order_id)
            return 1
        if (order.get("status") or "").lower() != "paid":
            logger.error("Заказ %s не оплачен (status=%s)", order_id, order.get("status"))
            return 1

        payment = await _fetch_payment_for_order(storage, order_id)
        if not payment:
            logger.error("Успешный платёж для заказа %s не найден", order_id)
            return 1

        winner_ids = await _fetch_recipient_ids(storage, order_id)
        delivered = len(winner_ids)
        if delivered <= 0:
            logger.error("Нет записей angel_pool_recipients для order_id=%s", order_id)
            return 1

        cur = (order.get("currency") or "RUB").upper()
        cur_label = ap_txt.currency_label(cur)
        amount_fmt = ap_txt.format_amount(float(order["amount"]), cur)
        admin_html = _build_admin_html(
            order=order,
            payment=payment,
            winner_ids=winner_ids,
            delivered=delivered,
        )

        group_topic = int(cfg.WISH_BOARD_DIGEST_TOPIC_ID or 0)
        group_id = int(cfg.CLUB_GROUP_ID or 0)
        admin_thread = int(cfg.PAYMENT_THREAD_ID or 0)

        if args.dry_run:
            print("=== DRY RUN ===")
            print(f"env: {args.env_file}")
            print(f"order_id: {order_id} payment_id: {payment['id']}")
            print(f"CLUB_GROUP_ID: {group_id} WISH_BOARD_DIGEST_TOPIC_ID: {group_topic}")
            print(f"ADMIN_CHANNEL_ID: {cfg.ADMIN_CHANNEL_ID} PAYMENT_THREAD_ID: {admin_thread}")
            print()
            print("--- админ-чат ---")
            print(admin_html)
            print()
            print("--- клубная группа (топик доски) ---")
            print(
                ap_txt.GROUP_TOPIC_HTML.format(
                    amount=amount_fmt,
                    currency_label=cur_label,
                    count=delivered,
                    count_word=ap_txt.count_word(delivered),
                )
            )
            return 0

        ok_admin = False
        if args.skip_admin:
            ok_admin = True
            logger.info("skip admin (--skip-admin)")
        elif cfg.ADMIN_CHANNEL_ID:
            ok_admin = await send_admin_html_message(
                bot,
                admin_html,
                thread_id=admin_thread if admin_thread > 0 else None,
            )
            if ok_admin:
                logger.info("OK admin channel thread=%s", admin_thread)
            else:
                logger.error("Не удалось отправить в админ-чат")
        else:
            logger.error("ADMIN_CHANNEL_ID не задан")

        ok_group = False
        if args.skip_group:
            ok_group = True
            logger.info("skip group (--skip-group)")
        elif group_id != 0 and group_topic > 0:
            ok_group = await wb_notify.post_angel_pool_donation(
                bot,
                amount=amount_fmt,
                currency_label=cur_label,
                count=delivered,
            )
            if ok_group:
                logger.info("OK club group=%s topic=%s", group_id, group_topic)
            else:
                logger.error("Не удалось отправить в топик доски добрых дел")
        else:
            logger.error(
                "CLUB_GROUP_ID или WISH_BOARD_DIGEST_TOPIC_ID не заданы (%s / %s)",
                group_id,
                group_topic,
            )

        if ok_admin and ok_group:
            print(
                f"Готово: уведомления отправлены (заказ {order_id}, "
                f"{delivered} продлений)."
            )
            return 0
        if ok_admin or ok_group:
            print("Частично: проверьте логи.")
            return 1
        return 1
    finally:
        await bot.session.close()
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Досылка уведомлений об ангельском взносе (админ + группа)."
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_PROD_ENV,
        help=f"Путь к .env прода (по умолчанию {DEFAULT_PROD_ENV})",
    )
    parser.add_argument(
        "--order-id",
        type=int,
        default=DEFAULT_ORDER_ID,
        help=f"ID заказа is_angel_pool (по умолчанию {DEFAULT_ORDER_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать тексты, ничего не отправлять",
    )
    parser.add_argument(
        "--skip-admin",
        action="store_true",
        help="Не слать в админ-чат (если уже отправляли)",
    )
    parser.add_argument(
        "--skip-group",
        action="store_true",
        help="Не слать в клубную группу",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
