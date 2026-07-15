"""
Mixin: заказы (`orders`).
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OrdersMixin:

    async def create_order(
        self,
        user_id: int,
        tariff_id: int,
        currency: str,
        amount: float,
        is_gift: bool = False,
        promo_campaign_guid: Optional[str] = None,
        gift_recipient_user_id: Optional[int] = None,
        is_angel_pool: bool = False,
        angel_pool_slots: Optional[int] = None,
    ) -> Optional[int]:
        """Создаёт новый заказ (обычный, подарочный, ангельский взнос или продление участнику)."""
        try:
            async with self.get_connection() as conn:
                order_id = await conn.fetchval(
                    """
                    INSERT INTO orders
                    (user_id, tariff_id, currency, amount, is_gift, status,
                     promo_campaign_guid, gift_recipient_user_id,
                     is_angel_pool, angel_pool_slots)
                    VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9)
                    RETURNING id
                    """,
                    user_id,
                    tariff_id,
                    currency,
                    amount,
                    is_gift,
                    promo_campaign_guid,
                    gift_recipient_user_id,
                    is_angel_pool,
                    angel_pool_slots,
                )
                logger.info(
                    f"✅ Order created: id={order_id}, user_id={user_id}, "
                    f"amount={amount} {currency}, is_gift={is_gift}, "
                    f"gift_recipient={gift_recipient_user_id}"
                )
                return order_id
        except Exception as e:
            logger.error(f"❌ Failed to create order: {e}")
            return None

    async def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает заказ + duration_days/tariff_name из tariffs."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT o.*,
                           t.duration_days,
                           t.name AS tariff_name,
                           t.type AS tariff_type
                    FROM orders o
                    JOIN tariffs t ON o.tariff_id = t.id
                    WHERE o.id = $1
                    """,
                    order_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get order {order_id}: {e}")
            return None

    async def update_order_paid(self, order_id: int, amount_rub: float) -> bool:
        """Помечает заказ как оплаченный и фиксирует сумму в рублях."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE orders
                    SET status = 'paid',
                        paid_at = NOW(),
                        amount_rub = $1
                    WHERE id = $2
                    """,
                    amount_rub, order_id,
                )
                logger.info(f"✅ Order {order_id} marked as paid")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update order {order_id}: {e}")
            return False
