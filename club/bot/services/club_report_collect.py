"""Сбор показателей и HTML ежедневного отчёта клуба (порт из legacy Adm club_report).

Используется asyncpg-пул приложения (`UserStorage.pool`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class ClubReportDailyCollector:
    """Те же запросы и `format_report`, что в legacy."""

    bot_name = "club"

    def __init__(self, pool: "asyncpg.Pool", *, club_group_id: int = 0) -> None:
        self._pool = pool
        self._club_group_id = club_group_id

    async def _fetch_scalar(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _fetch_row(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def _fetch_all(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]
    async def get_total_users(self) -> int:
        """Всего пользователей"""
        query = """
        SELECT COUNT(*) as total
        FROM users 
        WHERE is_active = true
        """
        result = await self._fetch_scalar(query)
        return result or 0
    
    async def get_active_users_yesterday(self) -> int:
        """Уникальные пользователи, взаимодействовавшие с ботом в ЛИЧКЕ за вчера."""
        query = """
        SELECT COUNT(DISTINCT user_id)
        FROM messages
        WHERE DATE(created_at) = CURRENT_DATE - INTERVAL '1 day'
          AND role = 'user'
          AND chat_id > 0
        """
        result = await self._fetch_scalar(query)
        return result or 0

    async def get_new_users_yesterday(self) -> int:
        """Новые пользователи за вчера"""
        query = """
        SELECT COUNT(*) as new
        FROM users 
        WHERE DATE(created_at) = CURRENT_DATE - INTERVAL '1 day'
        """
        result = await self._fetch_scalar(query)
        return result or 0

    async def get_club_group_active_yesterday(self) -> int:
        """Уникальные пользователи, проявившие активность в закрытой группе клуба за вчера."""
        if not self._club_group_id:
            return 0
        query = """
        SELECT COUNT(DISTINCT user_id)
        FROM messages
        WHERE DATE(created_at) = CURRENT_DATE - INTERVAL '1 day'
          AND chat_id = $1
          AND role = 'user'
        """
        result = await self._fetch_scalar(query, self._club_group_id)
        return result or 0
    
    async def get_pending_orders_yesterday(self) -> Dict[str, int]:
        """Уникальные юзеры, создавшие заказ(ы) вчера, но не оплатившие ни одного."""
        query = """
        WITH yesterday_users AS (
            SELECT DISTINCT user_id
            FROM orders
            WHERE DATE(created_at) = CURRENT_DATE - INTERVAL '1 day'
        ),
        paid_users AS (
            SELECT DISTINCT o.user_id
            FROM orders o
            JOIN payments p ON o.id = p.order_id
            WHERE DATE(o.created_at) = CURRENT_DATE - INTERVAL '1 day'
              AND p.status = 'succeeded'
        )
        SELECT COUNT(*) AS unique_users
        FROM yesterday_users yu
        WHERE yu.user_id NOT IN (SELECT user_id FROM paid_users)
        """
        row = await self._fetch_row(query)
        cnt = (row['unique_users'] if row else 0) or 0
        return {
            'count': cnt,
            'unique_users': cnt,
        }
    
    async def get_paid_orders_yesterday(self) -> Dict[str, Any]:
        """
        Оплаченные заказы за вчера (количество, уникальные пользователи, сумма)
        """
        query = """
        WITH yesterday_paid AS (
            SELECT 
                o.user_id,
                o.amount,
                p.amount_rub
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            WHERE DATE(o.paid_at) = CURRENT_DATE - INTERVAL '1 day'
              AND o.status = 'paid'
        )
        SELECT 
            COUNT(*) as orders_count,
            COUNT(DISTINCT user_id) as unique_users,
            COALESCE(SUM(amount_rub), 0) as total_amount
        FROM yesterday_paid
        """
        row = await self._fetch_row(query)
        return {
            'count': row['orders_count'] or 0,
            'unique_users': row['unique_users'] or 0,
            'total_amount': float(row['total_amount'] or 0)
        }
    
    async def get_tariff_breakdown_yesterday(self) -> Dict[str, Dict]:
        """Разбивка по тарифам за вчера (с уникальными пользователями)"""
        query = """
        SELECT 
            t.name as tariff_name,
            COUNT(o.id) as orders_count,
            COUNT(DISTINCT o.user_id) as unique_users,
            COALESCE(SUM(p.amount_rub), 0) as total_rub
        FROM orders o
        JOIN tariffs t ON o.tariff_id = t.id
        LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
        WHERE DATE(o.paid_at) = CURRENT_DATE - INTERVAL '1 day'
          AND o.status = 'paid'
        GROUP BY t.name
        ORDER BY orders_count DESC
        """
        rows = await self._fetch_all(query)
        
        result = {}
        for row in rows:
            result[row['tariff_name']] = {
                'orders': row['orders_count'],
                'unique_users': row['unique_users'],
                'amount': float(row['total_rub'])
            }
        return result

    async def _get_tariff_breakdown(
        self, date_filter_sql: str = ""
    ) -> Dict[str, Dict]:
        """Разбивка оплат по тарифам; ``date_filter_sql`` — AND-фрагмент для ``orders o``."""
        query = f"""
        SELECT
            t.name AS tariff_name,
            COUNT(o.id) AS orders_count,
            COUNT(DISTINCT o.user_id) AS unique_users,
            COALESCE(SUM(p.amount_rub), 0) AS total_rub
        FROM orders o
        JOIN tariffs t ON o.tariff_id = t.id
        LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
        WHERE o.status = 'paid'
          {date_filter_sql}
        GROUP BY t.name
        ORDER BY orders_count DESC, t.name
        """
        rows = await self._fetch_all(query)
        result: Dict[str, Dict] = {}
        for row in rows:
            result[row["tariff_name"]] = {
                "orders": row["orders_count"],
                "unique_users": row["unique_users"],
                "amount": float(row["total_rub"] or 0),
            }
        return result

    async def get_tariff_breakdown_last_30_days(self) -> Dict[str, Dict]:
        """Оплаты по тарифам за последние 30 календарных дней (без сегодня)."""
        return await self._get_tariff_breakdown(
            """
            AND DATE(o.paid_at) >= CURRENT_DATE - INTERVAL '30 days'
            AND DATE(o.paid_at) < CURRENT_DATE
            """
        )

    async def get_tariff_breakdown_all_time(self) -> Dict[str, Dict]:
        """Оплаты по тарифам за всё время."""
        return await self._get_tariff_breakdown("")
    
    async def get_paid_orders_month(self) -> Dict[str, int]:
        """
        Оплаченные заказы за текущий месяц (количество и уникальные пользователи)
        """
        query = """
        SELECT 
            COUNT(*) as orders_count,
            COUNT(DISTINCT o.user_id) as unique_users
        FROM orders o
        WHERE DATE(o.paid_at) >= DATE_TRUNC('month', CURRENT_DATE)
          AND DATE(o.paid_at) < CURRENT_DATE
          AND o.status = 'paid'
        """
        row = await self._fetch_row(query)
        return {
            'count': row['orders_count'] or 0,
            'unique_users': row['unique_users'] or 0
        }
    
    async def get_total_amount_month(self) -> float:
        """Сумма оплат за текущий месяц (в рублях)"""
        query = """
        SELECT COALESCE(SUM(amount_rub), 0) as total
        FROM payments 
        WHERE DATE(created_at) >= DATE_TRUNC('month', CURRENT_DATE)
          AND DATE(created_at) < CURRENT_DATE
          AND status = 'succeeded'
        """
        result = await self._fetch_scalar(query)
        return float(result or 0)
    
    async def get_tariff_breakdown_month(self) -> Dict[str, Dict]:
        """Разбивка по тарифам за текущий месяц (с уникальными пользователями)"""
        query = """
        SELECT 
            t.name as tariff_name,
            COUNT(o.id) as orders_count,
            COUNT(DISTINCT o.user_id) as unique_users,
            COALESCE(SUM(p.amount_rub), 0) as total_rub
        FROM orders o
        JOIN tariffs t ON o.tariff_id = t.id
        LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
        WHERE DATE(o.paid_at) >= DATE_TRUNC('month', CURRENT_DATE)
          AND DATE(o.paid_at) < CURRENT_DATE
          AND o.status = 'paid'
        GROUP BY t.name
        ORDER BY orders_count DESC
        """
        rows = await self._fetch_all(query)
        
        result = {}
        for row in rows:
            result[row['tariff_name']] = {
                'orders': row['orders_count'],
                'unique_users': row['unique_users'],
                'amount': float(row['total_rub'])
            }
        return result
    
    async def get_duplicate_analysis_yesterday(self) -> Dict[str, Any]:
        """
        Анализ дублей заказов за вчера
        """
        query = """
        WITH user_orders AS (
            SELECT 
                o.user_id,
                COUNT(*) as orders_count,
                COUNT(CASE WHEN o.status = 'paid' OR p.status = 'succeeded' THEN 1 END) as paid_orders
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id
            WHERE DATE(o.created_at) = CURRENT_DATE - INTERVAL '1 day'
            GROUP BY o.user_id
        )
        SELECT 
            COUNT(*) as total_users,
            COUNT(*) FILTER (WHERE orders_count = 1) as users_with_1_order,
            COUNT(*) FILTER (WHERE orders_count >= 2) as users_with_multiple,
            MAX(orders_count) as max_orders
        FROM user_orders
        """
        row = await self._fetch_row(query)
        if row is None:
            return {
                "total_users": 0,
                "users_with_1_order": 0,
                "users_with_multiple": 0,
                "max_orders": 0,
            }
        return {
            "total_users": row["total_users"] or 0,
            "users_with_1_order": row["users_with_1_order"] or 0,
            "users_with_multiple": row["users_with_multiple"] or 0,
            "max_orders": row["max_orders"] or 0,
        }

    async def get_all_metrics(self) -> Dict[str, Any]:
        """Сбор всех метрик для отчета"""
        yesterday = datetime.now() - timedelta(days=1)
        month_name = yesterday.strftime('%B')
        month_day = yesterday.day
        
        # Лицензии (в т.ч. для снепшота в БД)
        active_licenses = await self.get_active_licenses_count()
        users_expiring = await self.get_users_expiring_soon()
        users_expired = await self.get_users_with_expired_license()
        users_expired_test_drive = await self.get_expired_license_promo_test1week_only_count()

        # Получаем данные о выручке по месяцам
        monthly_revenue = await self.get_monthly_revenue()
        total_revenue = await self.get_total_revenue()
        pending_orders = await self.get_pending_orders_yesterday()
        paid_orders = await self.get_paid_orders_yesterday()
        referral_sources = await self.get_referral_sources_yesterday()
        paid_breakdown = await self.get_paid_orders_breakdown_yesterday()

        new_users_by_source = await self.get_new_users_by_source_today()
        new_users_by_source_month = await self.get_new_users_by_source_month()
        sales_by_source_month = await self.get_sales_by_source_month()

        # 🔥 НОВЫЕ ВЫЗОВЫ (по типам)
        new_users_by_type = await self.get_new_users_by_type_today()
        new_users_by_type_month = await self.get_new_users_by_type_month()
        sales_by_type_month = await self.get_sales_by_type_month()
        
        metrics = {
            "period": f"Данные за {yesterday.strftime('%d.%m.%Y')}",
            "month_period": f"{month_name} (1-{month_day}.{yesterday.strftime('%m.%Y')})",
            
            "total_users": await self.get_total_users(),
            "active_users": await self.get_active_users_yesterday(),
            "new_users": await self.get_new_users_yesterday(),
            "club_group_active": await self.get_club_group_active_yesterday(),
            
            "pending_orders": await self.get_pending_orders_yesterday(),
            "paid_orders": await self.get_paid_orders_yesterday(),
            "paid_breakdown": paid_breakdown,
            "total_amount": paid_orders['total_amount'],
            "tariff_breakdown": await self.get_tariff_breakdown_yesterday(),
            "tariff_breakdown_30d": await self.get_tariff_breakdown_last_30_days(),
            "tariff_breakdown_all": await self.get_tariff_breakdown_all_time(),
            
            "month_paid_orders": await self.get_paid_orders_month(),
            "month_total_amount": await self.get_total_amount_month(),
            "month_tariff_breakdown": await self.get_tariff_breakdown_month(),
            
            "active_licenses": active_licenses,
            "users_expiring": users_expiring,
            "users_expired": users_expired,
            "users_expired_test_drive": users_expired_test_drive,
            "monthly_revenue": monthly_revenue,
            "total_revenue": total_revenue,
            "referral_sources": referral_sources,

            "new_users_by_source": new_users_by_source,
            "new_users_by_source_month": new_users_by_source_month,
            "sales_by_source_month": sales_by_source_month,

            # По типам (агрегированные группы)
            "new_users_by_type": new_users_by_type,
            "new_users_by_type_month": new_users_by_type_month,
            "sales_by_type_month": sales_by_type_month,
        }
        
        pd = metrics["paid_orders"]
        paid_cnt = pd["count"] if isinstance(pd, dict) else pd

        logger.info(
            "📊 Метрики %s: активных=%s оплат=%s сумма=%.0f ₽",
            self.bot_name,
            metrics["active_users"],
            paid_cnt,
            metrics["total_amount"],
        )
        
        return metrics
    
    def format_report(self, metrics: dict) -> str:
        """Форматирование отчета"""
        
        # Получаем данные из словарей
        pending = metrics.get('pending_orders', {})
        paid = metrics.get('paid_orders', {})
        paid_breakdown = metrics.get('paid_breakdown', {})
        
        pending_count = pending.get('count', 0)
        paid_count = paid.get('count', 0)
        paid_total = paid.get('total_amount', 0)
        
        # Получаем данные о новых и продлениях
        new_count = paid_breakdown.get('new', {}).get('count', 0) if paid_breakdown else 0
        renewal_count = paid_breakdown.get('renewal', {}).get('count', 0) if paid_breakdown else 0
        
        # Формируем строки по тарифам
        tariff_lines = []
        for name, data in metrics.get('tariff_breakdown', {}).items():
            tariff_lines.append(f"• {name}: {data['orders']} заказов ({data['amount']:,.0f} ₽)")
        
        month_tariff_lines = []
        for name, data in metrics.get('month_tariff_breakdown', {}).items():
            month_tariff_lines.append(f"• {name}: {data['orders']} заказов ({data['amount']:,.0f} ₽)")
        
        tariff_text = "\n".join(tariff_lines) if tariff_lines else "• Нет оплат"
        month_tariff_text = "\n".join(month_tariff_lines) if month_tariff_lines else "• Нет оплат"

        expiring_lines = []
        for row in metrics.get("users_expiring", []) or []:
            days = int(row["days_left"])
            count = int(row["user_count"] or 0)
            td = int(row.get("td_count") or 0)
            expiring_lines.append(f"• {days} дн.: {count} чел. (ТД: {td} чел.)")
        expiring_text = "\n".join(expiring_lines) if expiring_lines else "• Нет"

        # Формируем блок выручки по месяцам
        revenue_lines = []
        for row in metrics.get('monthly_revenue', []):
            month_name = row['month']
            orders = row['orders_count']
            amount = row['total_amount']
            revenue_lines.append(f"• {month_name}: {amount:,.0f} ₽ ({orders} заказов)")
        revenue_text = "\n".join(revenue_lines) if revenue_lines else "• Нет данных"
        
        # Формируем блок аналитики по источникам
        referral_lines = []
        for row in metrics.get('referral_sources', []):
            source_name = row['source_name']
            users = row['unique_users']
            orders = row['total_orders']
            amount = row['total_amount']
            referral_lines.append(f"• {source_name}: {users} чел. ({orders} заказов, {amount:,.0f} ₽)")
        referral_text = "\n".join(referral_lines) if referral_lines else "• Нет данных"

        # Формируем блок источников новых пользователей (за вчера)
        new_users_source_lines = []
        for row in metrics.get('new_users_by_source', []):
            new_users_source_lines.append(f"• {row['source_name']}: {row['new_users']} чел.")

        new_users_source_text = "\n".join(new_users_source_lines) if new_users_source_lines else "• Нет данных"

        # Формируем блок источников новых пользователей (за месяц)
        new_users_source_month_lines = []
        for row in metrics.get('new_users_by_source_month', []):
            new_users_source_month_lines.append(f"• {row['source_name']}: {row['new_users']} чел.")

        new_users_source_month_text = "\n".join(new_users_source_month_lines) if new_users_source_month_lines else "• Нет данных"

        # Формируем блок продаж по источникам (за месяц)
        sales_source_lines = []
        for row in metrics.get('sales_by_source_month', []):
            sales_source_lines.append(f"• {row['source_name']}: {row['unique_users']} чел. ({row['orders_count']} заказов, {row['total_amount']:,.0f} ₽)")

        sales_source_text = "\n".join(sales_source_lines) if sales_source_lines else "• Нет данных"

        # Формируем блок источников НОВЫХ ПОЛЬЗОВАТЕЛЕЙ по ТИПАМ (за вчера)
        new_users_type_lines = []
        for row in metrics.get('new_users_by_type', []):
            type_name = row['source_type']
            type_display = {
                'tg': '📱 Telegram',
                'biblia_bot': '📖 Библия бот',
                'other': '🔗 Другие'
            }.get(type_name, type_name)
            new_users_type_lines.append(f"• {type_display}: {row['new_users']} чел.")

        new_users_type_text = "\n".join(new_users_type_lines) if new_users_type_lines else "• Нет данных"

        # Формируем блок ПРОДАЖ ПО ТИПАМ (за месяц)
        sales_type_lines = []
        for row in metrics.get('sales_by_type_month', []):
            type_name = row['source_type']
            type_display = {
                'tg': '📱 Telegram',
                'biblia_bot': '📖 Библия бот',
                'other': '🔗 Другие'
            }.get(type_name, type_name)
            sales_type_lines.append(f"• {type_display}: {row['unique_users']} чел. ({row['orders_count']} заказов, {row['total_amount']:,.0f} ₽)")

        sales_type_text = "\n".join(sales_type_lines) if sales_type_lines else "• Нет данных"

        # Формируем блок НОВЫХ ПОЛЬЗОВАТЕЛЕЙ ПО ТИПАМ (за месяц)
        new_users_type_month_lines = []
        for row in metrics.get('new_users_by_type_month', []):
            type_name = row['source_type']
            type_display = {
                'tg': '📱 Telegram',
                'biblia_bot': '📖 Библия бот',
                'other': '🔗 Другие'
            }.get(type_name, type_name)
            new_users_type_month_lines.append(f"• {type_display}: {row['new_users']} чел.")

        new_users_type_month_text = "\n".join(new_users_type_month_lines) if new_users_type_month_lines else "• Нет данных"

        total_revenue = metrics.get('total_revenue', 0)
        month_total_amount = metrics.get('month_total_amount', 0)
        month_paid_orders = metrics.get('month_paid_orders', {}).get('count', 0) if isinstance(metrics.get('month_paid_orders'), dict) else metrics.get('month_paid_orders', 0)
        month_unique_users = metrics.get('month_paid_orders', {}).get('unique_users', 0) if isinstance(metrics.get('month_paid_orders'), dict) else 0
        
        report = f"""<b>🤖 КЛУБ</b>
<i>{metrics['period']}</i>
• Всего пользователей: {metrics['total_users']:,}
• Участников клуба (активная лицензия): {metrics.get('active_licenses', 0):,}

<b>👥 АКТИВНОСТЬ (за вчера)</b>
🤖 В боте:
• Активных: {metrics['active_users']:,}
• Новых: {metrics['new_users']:,}
💃 В клубе: {metrics.get('club_group_active', 0):,}

<b>📊 НОВЫЕ ПОЛЬЗОВАТЕЛИ ПО ИСТОЧНИКАМ (за вчера)</b>
{new_users_source_text}

<b>💰 ЗАКАЗЫ (за вчера)</b>
• Неоплаченных: {pending_count}
• Оплаченных: {paid_count}
• • Новых: {new_count}
• • Продлений: {renewal_count}
• Сумма оплат: {paid_total:,.0f} ₽

<b>📊 ПО ТАРИФАМ (за вчера)</b>
{tariff_text}

<b>📊 НОВЫЕ КЛИЕНТЫ ПО ИСТОЧНИКАМ (за вчера)</b>
{referral_text}

<b>💰 ЗА МЕСЯЦ ({metrics['month_period']})</b>
• Оплаченных заказов: {month_paid_orders} (уник. пользователей: {month_unique_users})
• Сумма оплат: {month_total_amount:,.0f} ₽

<b>📊 ПРОДАЖИ ПО ИСТОЧНИКАМ (за месяц)</b>
{sales_type_text}

<b>📊 НОВЫЕ ПОЛЬЗОВАТЕЛИ ПО ИСТОЧНИКАМ (за месяц)</b>
{new_users_type_month_text}


<b>📊 ПО ТАРИФАМ (за месяц)</b>
{month_tariff_text}

<b>⏰ ЗАКАНЧИВАЮТСЯ ЛИЦЕНЗИИ (ближайшие 7 дней)</b>
{expiring_text}

<b>⚠️ ПРОСРОЧЕННЫЕ ЛИЦЕНЗИИ</b>
• Всего: {metrics.get('users_expired', 0)} чел.
• из них ТД: {metrics.get('users_expired_test_drive', 0)} чел.

<b>💰 ВСЕГО ВЫРУЧКА ПРОЕКТА</b>
{revenue_text}
━━━━━━━━━━━━━━━━━━━━━
<b>📊 ИТОГО:</b> {total_revenue:,.0f} ₽"""

        return report

    async def get_users_expiring_soon(self) -> List[Dict[str, Any]]:
        """
        Возвращает по дням (1..7): сколько активных лицензий истекает и сколько из них «ТД»
        (только успешные оплаты тарифов promo_test1week*).
        """
        query = """
        WITH expiring_users AS (
            SELECT DISTINCT user_id
            FROM license
            WHERE expires_at >= CURRENT_DATE
              AND expires_at < CURRENT_DATE + INTERVAL '7 days'
              AND status = 'active'
        ),
        test_drive_only AS (
            SELECT eu.user_id
            FROM expiring_users eu
            WHERE EXISTS (
                SELECT 1
                FROM orders o
                JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                JOIN tariffs t ON t.id = o.tariff_id
                WHERE o.user_id = eu.user_id
                  AND o.status = 'paid'
                  AND COALESCE(t.type, '') LIKE 'promo_test1week%'
            )
            AND NOT EXISTS (
                SELECT 1
                FROM orders o2
                JOIN payments p2 ON p2.order_id = o2.id AND p2.status = 'succeeded'
                JOIN tariffs t2 ON t2.id = o2.tariff_id
                WHERE o2.user_id = eu.user_id
                  AND o2.status = 'paid'
                  AND COALESCE(t2.type, '') NOT LIKE 'promo_test1week%'
            )
        )
        SELECT
            EXTRACT(DAY FROM (l.expires_at - CURRENT_DATE)) + 1 AS days_left,
            COUNT(DISTINCT l.user_id) AS user_count,
            COUNT(DISTINCT CASE WHEN tdo.user_id IS NOT NULL THEN l.user_id END) AS td_count
        FROM license l
        LEFT JOIN test_drive_only tdo ON tdo.user_id = l.user_id
        WHERE l.expires_at >= CURRENT_DATE
          AND l.expires_at < CURRENT_DATE + INTERVAL '7 days'
          AND l.status = 'active'
        GROUP BY 1
        ORDER BY 1
        """
        return await self._fetch_all(query)


    async def get_users_with_expired_license(self) -> int:
        """
        Возвращает количество пользователей с просроченной лицензией
        """
        query = """
        SELECT COUNT(DISTINCT user_id) as expired_count
        FROM license
        WHERE status = 'expired'
        """
        result = await self._fetch_scalar(query)
        return result or 0

    async def get_expired_license_promo_test1week_only_count(self) -> int:
        """
        Просроченная лицензия (`status='expired'`) и по всем успешным оплатам только «тест неделя»:
        тип тарифа `promo_test1week` или с префиксом этого имени (напр. promo_test1week_benefit).
        """
        query = """
        SELECT COUNT(*)::bigint AS cnt
        FROM (
            SELECT DISTINCT l.user_id
            FROM license l
            WHERE l.status = 'expired'
              AND EXISTS (
                  SELECT 1
                  FROM orders o
                  JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                  JOIN tariffs t ON t.id = o.tariff_id
                  WHERE o.user_id = l.user_id
                    AND o.status = 'paid'
                    AND COALESCE(t.type, '') LIKE 'promo_test1week%'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM orders o2
                  JOIN payments p2 ON p2.order_id = o2.id AND p2.status = 'succeeded'
                  JOIN tariffs t2 ON t2.id = o2.tariff_id
                  WHERE o2.user_id = l.user_id
                    AND o2.status = 'paid'
                    AND COALESCE(t2.type, '') NOT LIKE 'promo_test1week%'
              )
        ) expired_test_drive
        """
        result = await self._fetch_scalar(query)
        return int(result or 0)

    async def get_promo_week_to_base_funnel_totals(self) -> Dict[str, Any]:
        """
        Эффективность теста: пользователи, когда-либо оплатившие promo_test1week*,
        и сколько из них позже оплатили заказ с типом тарифа base (строго один тип в БД).
        """
        query = """
        WITH first_test_pay AS (
            SELECT DISTINCT ON (o.user_id)
                o.user_id,
                o.paid_at AS first_test_at
            FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            JOIN tariffs t ON t.id = o.tariff_id
            WHERE o.status = 'paid'
              AND COALESCE(t.type, '') LIKE 'promo_test1week%'
            ORDER BY o.user_id, o.paid_at ASC
        ),
        converted AS (
            SELECT ft.user_id
            FROM first_test_pay ft
            WHERE EXISTS (
                SELECT 1
                FROM orders o2
                JOIN payments p2 ON p2.order_id = o2.id AND p2.status = 'succeeded'
                JOIN tariffs t2 ON t2.id = o2.tariff_id
                WHERE o2.user_id = ft.user_id
                  AND o2.status = 'paid'
                  AND COALESCE(t2.type, '') = 'base'
                  AND o2.paid_at > ft.first_test_at
            )
        )
        SELECT
            (SELECT COUNT(*)::bigint FROM first_test_pay) AS test_buyers_total,
            (SELECT COUNT(*)::bigint FROM converted) AS converted_to_base
        """
        row = await self._fetch_row(query)
        total = int((row or {}).get("test_buyers_total") or 0)
        conv = int((row or {}).get("converted_to_base") or 0)
        pct = (100.0 * conv / total) if total else 0.0
        return {
            "test_buyers_total": total,
            "converted_to_base": conv,
            "conversion_pct": round(pct, 2),
        }

    async def get_expired_license_by_acquisition_source(self) -> List[Dict[str, Any]]:
        """
        Пользователи с просрочкой: разрез по первому /start ref_ (как в блоках новых по источникам).
        """
        query = """
        WITH expired_u AS (
            SELECT DISTINCT user_id FROM license WHERE status = 'expired'
        ),
        first_ref AS (
            SELECT DISTINCT ON (eu.user_id)
                eu.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') AS ref_key
            FROM expired_u eu
            LEFT JOIN interaction_logs il ON il.user_id = eu.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            ORDER BY eu.user_id, il.created_at ASC NULLS LAST
        )
        SELECT
            COALESCE(rk.name, COALESCE(fr.ref_key, 'БЕЗ РЕФЕРАЛКИ')) AS source_name,
            COUNT(*)::bigint AS user_count
        FROM first_ref fr
        LEFT JOIN ref_keys rk ON fr.ref_key = rk.ref_key
        GROUP BY
            COALESCE(fr.ref_key, 'БЕЗ РЕФЕРАЛКИ'),
            COALESCE(rk.name, COALESCE(fr.ref_key, 'БЕЗ РЕФЕРАЛКИ'))
        ORDER BY user_count DESC
        """
        return await self._fetch_all(query)

    async def get_expired_license_by_source_type(self) -> List[Dict[str, Any]]:
        """То же множество с просрочкой по типу ref_keys.type (tg / biblia_bot / …)."""
        query = """
        WITH expired_u AS (
            SELECT DISTINCT user_id FROM license WHERE status = 'expired'
        ),
        first_ref AS (
            SELECT DISTINCT ON (eu.user_id)
                eu.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') AS ref_key
            FROM expired_u eu
            LEFT JOIN interaction_logs il ON il.user_id = eu.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            ORDER BY eu.user_id, il.created_at ASC NULLS LAST
        )
        SELECT
            COALESCE(rk.type, 'other') AS source_type,
            COUNT(*)::bigint AS user_count
        FROM first_ref fr
        LEFT JOIN ref_keys rk ON fr.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.type, 'other')
        ORDER BY user_count DESC
        """
        return await self._fetch_all(query)

    async def get_monthly_revenue(self) -> List[Dict[str, Any]]:
        """Выручка по полным календарным месяцам (для legacy-отчёта)."""
        query = """
        SELECT
            TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM') AS month,
            DATE_TRUNC('month', created_at) AS month_date,
            COUNT(DISTINCT order_id) AS orders_count,
            COUNT(DISTINCT user_id) AS unique_users,
            COALESCE(SUM(amount_rub), 0) AS total_amount
        FROM payments
        WHERE status = 'succeeded'
        GROUP BY DATE_TRUNC('month', created_at)
        ORDER BY month_date DESC
        """
        return await self._fetch_all(query)

    async def get_monthly_revenue_paced(self) -> List[Dict[str, Any]]:
        """
        Текущий месяц отчёта: выручка с 1-го по дату отчёта (вчера, МСК).
        Прошлые месяцы: полная выручка и заказы за календарный месяц.
        Δ на прошлых месяцах: (текущий месяц, 1..день отчёта) − (тот же интервал в том месяце).
        """
        query = """
        WITH anchor AS (
            SELECT
                ((NOW() AT TIME ZONE 'Europe/Moscow')::date - 1) AS report_date,
                DATE_TRUNC(
                    'month',
                    (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
                )::date AS cur_month_start,
                EXTRACT(
                    DAY FROM (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
                )::int AS cmp_day
        ),
        first_pay AS (
            SELECT DATE_TRUNC(
                'month',
                MIN((p.created_at AT TIME ZONE 'Europe/Moscow')::date)
            )::date AS m_start
            FROM payments p
            WHERE p.status = 'succeeded'
        ),
        month_starts AS (
            SELECT (DATE_TRUNC('month', gs))::date AS m_start
            FROM anchor a
            CROSS JOIN first_pay fp
            CROSS JOIN LATERAL GENERATE_SERIES(
                fp.m_start,
                a.cur_month_start,
                INTERVAL '1 month'
            ) AS gs
            WHERE fp.m_start IS NOT NULL
        ),
        months AS (
            SELECT
                ms.m_start,
                TO_CHAR(ms.m_start, 'YYYY-MM') AS month,
                (ms.m_start = a.cur_month_start) AS is_current_month,
                ms.m_start AS partial_from,
                LEAST(
                    ms.m_start + (a.cmp_day - 1),
                    (
                        DATE_TRUNC('month', ms.m_start::timestamp)
                        + INTERVAL '1 month - 1 day'
                    )::date
                ) AS partial_to,
                a.cmp_day,
                a.report_date
            FROM month_starts ms
            CROSS JOIN anchor a
        ),
        partial AS (
            SELECT
                m.month,
                m.m_start,
                m.is_current_month,
                m.cmp_day,
                m.report_date,
                COALESCE(SUM(p.amount_rub), 0) AS partial_amount,
                COUNT(DISTINCT p.order_id)::int AS partial_orders
            FROM months m
            LEFT JOIN payments p
              ON p.status = 'succeeded'
             AND (p.created_at AT TIME ZONE 'Europe/Moscow')::date >= m.partial_from
             AND (p.created_at AT TIME ZONE 'Europe/Moscow')::date <= m.partial_to
            GROUP BY m.month, m.m_start, m.is_current_month, m.cmp_day, m.report_date
        ),
        full_month AS (
            SELECT
                m.m_start,
                COALESCE(SUM(p.amount_rub), 0) AS full_amount,
                COUNT(DISTINCT p.order_id)::int AS full_orders
            FROM months m
            LEFT JOIN payments p
              ON p.status = 'succeeded'
             AND (p.created_at AT TIME ZONE 'Europe/Moscow')::date >= m.m_start
             AND (p.created_at AT TIME ZONE 'Europe/Moscow')::date
                 < (DATE_TRUNC('month', m.m_start::timestamp) + INTERVAL '1 month')::date
            GROUP BY m.m_start
        ),
        cur AS (
            SELECT partial_amount AS cur_partial_amount
            FROM partial
            WHERE is_current_month
            LIMIT 1
        )
        SELECT
            p.month,
            p.cmp_day,
            p.report_date,
            p.is_current_month,
            CASE
                WHEN p.is_current_month THEN p.partial_amount
                ELSE f.full_amount
            END AS total_amount,
            CASE
                WHEN p.is_current_month THEN p.partial_orders
                ELSE f.full_orders
            END AS orders_count,
            CASE
                WHEN p.is_current_month THEN NULL
                ELSE c.cur_partial_amount - p.partial_amount
            END AS delta_amount
        FROM partial p
        JOIN full_month f ON f.m_start = p.m_start
        CROSS JOIN cur c
        ORDER BY p.m_start DESC
        """
        rows = await self._fetch_all(query)
        for row in rows:
            row["total_amount"] = float(row.get("total_amount") or 0)
            da = row.get("delta_amount")
            row["delta_amount"] = float(da) if da is not None else None
            row["is_current_month"] = bool(row.get("is_current_month"))
        return rows


    async def get_total_revenue(self) -> float:
        """
        Возвращает общую сумму выручки за всё время
        """
        query = """
        SELECT COALESCE(SUM(amount_rub), 0) as total
        FROM payments
        WHERE status = 'succeeded'
        """
        result = await self._fetch_scalar(query)
        return float(result or 0)            

    async def get_active_licenses_count(self) -> int:
        """
        Возвращает количество активных лицензий (участников клуба)
        """
        query = """
        SELECT COUNT(DISTINCT user_id) as active_count
        FROM license
        WHERE status = 'active' and license_type <> 'bonus'
        AND expires_at > NOW()
        """
        result = await self._fetch_scalar(query)
        return result or 0    

    async def get_referral_sources_yesterday(self) -> List[Dict[str, Any]]:
        """
        Аналитика по источникам (реферальным ключам) для новых заказов за вчера
        """
        query = """
        WITH user_first_order AS (
            SELECT DISTINCT ON (o.user_id)
                o.user_id,
                o.paid_at,
                p.amount_rub
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            WHERE o.status = 'paid'
            AND p.status = 'succeeded'
            ORDER BY o.user_id, o.paid_at ASC
        ),
        new_orders_yesterday AS (
            SELECT *
            FROM user_first_order
            WHERE DATE(paid_at) = CURRENT_DATE - INTERVAL '1 day'
        ),
        user_last_ref AS (
            SELECT DISTINCT ON (il.user_id)
                il.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM interaction_logs il
            JOIN new_orders_yesterday no ON il.user_id = no.user_id
            WHERE il.event_type = 'received'
            AND il.data->>'text' LIKE '%/start ref_%'
            AND il.created_at <= no.paid_at  -- 🔥 ВАЖНО: только до даты оплаты
            ORDER BY il.user_id, il.created_at DESC
        ),
        paid_orders AS (
            SELECT 
                user_id,
                COUNT(*) as orders_count,
                SUM(amount_rub) as total_amount
            FROM new_orders_yesterday
            GROUP BY user_id
        )
        SELECT 
            COALESCE(rk.name, COALESCE(ulr.ref_key, 'БЕЗ РЕФЕРАЛКИ')) as source_name,
            COUNT(DISTINCT po.user_id) as unique_users,
            SUM(po.orders_count) as total_orders,
            SUM(po.total_amount) as total_amount
        FROM paid_orders po
        LEFT JOIN user_last_ref ulr ON po.user_id = ulr.user_id
        LEFT JOIN ref_keys rk ON ulr.ref_key = rk.ref_key
        GROUP BY COALESCE(ulr.ref_key, 'БЕЗ РЕФЕРАЛКИ'), COALESCE(rk.name, COALESCE(ulr.ref_key, 'БЕЗ РЕФЕРАЛКИ'))
        ORDER BY total_amount DESC
        """
        return await self._fetch_all(query)

    async def get_paid_orders_breakdown_yesterday(self) -> Dict[str, Any]:
        """
        Оплаченные заказы за вчера с разделением на новые и продления
        """
        query = """
        WITH user_first_order AS (
            SELECT DISTINCT ON (user_id)
                user_id,
                id as first_order_id
            FROM orders
            WHERE status = 'paid'
            ORDER BY user_id, paid_at ASC
        ),
        orders_yesterday AS (
            SELECT 
                o.user_id,
                o.id,
                p.amount_rub,
                CASE 
                    WHEN ufo.first_order_id = o.id THEN 'new'
                    ELSE 'renewal'
                END as order_type
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            LEFT JOIN user_first_order ufo ON o.user_id = ufo.user_id
            WHERE DATE(o.paid_at) = CURRENT_DATE - INTERVAL '1 day'
            AND o.status = 'paid'
            AND p.status = 'succeeded'
        )
        SELECT 
            order_type,
            COUNT(*) as orders_count,
            COUNT(DISTINCT user_id) as unique_users,
            COALESCE(SUM(amount_rub), 0) as total_amount
        FROM orders_yesterday
        GROUP BY order_type
        """
        rows = await self._fetch_all(query)
        
        result = {
            'new': {'count': 0, 'unique_users': 0, 'total_amount': 0.0},
            'renewal': {'count': 0, 'unique_users': 0, 'total_amount': 0.0}
        }
        
        for row in rows:
            if row['order_type'] == 'new':
                result['new'] = {
                    'count': row['orders_count'],
                    'unique_users': row['unique_users'],
                    'total_amount': float(row['total_amount'])
                }
            elif row['order_type'] == 'renewal':
                result['renewal'] = {
                    'count': row['orders_count'],
                    'unique_users': row['unique_users'],
                    'total_amount': float(row['total_amount'])
                }
        
        return result

    async def get_new_users_by_source_today(self) -> List[Dict[str, Any]]:
        """
        Аналитика источников для НОВЫХ пользователей за вчера
        (первые /start ref_xxx)
        """
        query = """
        WITH first_start AS (
            SELECT DISTINCT ON (u.user_id)
                u.user_id,
                u.created_at as user_reg_date,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM users u
            LEFT JOIN interaction_logs il ON u.user_id = il.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            WHERE DATE(u.created_at) = CURRENT_DATE - INTERVAL '1 day'
            AND u.is_active = true
            ORDER BY u.user_id, il.created_at ASC
        )
        SELECT 
            COALESCE(rk.name, COALESCE(fs.ref_key, 'БЕЗ РЕФЕРАЛКИ')) as source_name,
            COUNT(*) as new_users
        FROM first_start fs
        LEFT JOIN ref_keys rk ON fs.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.name, COALESCE(fs.ref_key, 'БЕЗ РЕФЕРАЛКИ'))
        ORDER BY new_users DESC
        """
        return await self._fetch_all(query)


    async def get_sales_by_source_month(self) -> List[Dict[str, Any]]:
        """
        Аналитика продаж по источникам за текущий месяц
        (первые заказы пользователей с привязкой к ref_key)
        """
        query = """
        WITH user_first_order AS (
            SELECT DISTINCT ON (o.user_id)
                o.user_id,
                o.paid_at,
                p.amount_rub,
                DATE_TRUNC('month', o.paid_at) as paid_month
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            WHERE o.status = 'paid'
            AND p.status = 'succeeded'
            AND DATE_TRUNC('month', o.paid_at) = DATE_TRUNC('month', CURRENT_DATE)
            ORDER BY o.user_id, o.paid_at ASC
        ),
        user_ref AS (
            SELECT DISTINCT ON (u.user_id)
                u.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM users u
            LEFT JOIN interaction_logs il ON u.user_id = il.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            ORDER BY u.user_id, il.created_at ASC
        )
        SELECT 
            COALESCE(rk.name, COALESCE(ur.ref_key, 'БЕЗ РЕФЕРАЛКИ')) as source_name,
            COUNT(DISTINCT ufo.user_id) as unique_users,
            COUNT(*) as orders_count,
            COALESCE(SUM(ufo.amount_rub), 0) as total_amount
        FROM user_first_order ufo
        LEFT JOIN user_ref ur ON ufo.user_id = ur.user_id
        LEFT JOIN ref_keys rk ON ur.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.name, COALESCE(ur.ref_key, 'БЕЗ РЕФЕРАЛКИ'))
        ORDER BY total_amount DESC
        """
        return await self._fetch_all(query)


    async def get_new_users_by_source_month(self) -> List[Dict[str, Any]]:
        """
        Аналитика источников для НОВЫХ пользователей за текущий месяц
        (первые /start ref_xxx)
        """
        query = """
        WITH first_start AS (
            SELECT DISTINCT ON (u.user_id)
                u.user_id,
                u.created_at as user_reg_date,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM users u
            LEFT JOIN interaction_logs il ON u.user_id = il.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            WHERE DATE_TRUNC('month', u.created_at) = DATE_TRUNC('month', CURRENT_DATE)
            AND u.is_active = true
            ORDER BY u.user_id, il.created_at ASC
        )
        SELECT 
            COALESCE(rk.name, COALESCE(fs.ref_key, 'БЕЗ РЕФЕРАЛКИ')) as source_name,
            COUNT(*) as new_users
        FROM first_start fs
        LEFT JOIN ref_keys rk ON fs.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.name, COALESCE(fs.ref_key, 'БЕЗ РЕФЕРАЛКИ'))
        ORDER BY new_users DESC
        """
        return await self._fetch_all(query)        


    async def get_new_users_by_type_today(self) -> List[Dict[str, Any]]:
        """
        Аналитика источников по ТИПАМ для НОВЫХ пользователей за вчера
        """
        query = """
        WITH first_start AS (
            SELECT DISTINCT ON (u.user_id)
                u.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM users u
            LEFT JOIN interaction_logs il ON u.user_id = il.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            WHERE DATE(u.created_at) = CURRENT_DATE - INTERVAL '1 day'
            AND u.is_active = true
            ORDER BY u.user_id, il.created_at ASC
        )
        SELECT 
            COALESCE(rk.type, 'other') as source_type,
            COUNT(*) as new_users
        FROM first_start fs
        LEFT JOIN ref_keys rk ON fs.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.type, 'other')
        ORDER BY new_users DESC
        """
        return await self._fetch_all(query)


    async def get_new_users_by_type_month(self) -> List[Dict[str, Any]]:
        """
        Аналитика источников по ТИПАМ для НОВЫХ пользователей за текущий месяц
        """
        query = """
        WITH first_start AS (
            SELECT DISTINCT ON (u.user_id)
                u.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM users u
            LEFT JOIN interaction_logs il ON u.user_id = il.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            WHERE DATE_TRUNC('month', u.created_at) = DATE_TRUNC('month', CURRENT_DATE)
            AND u.is_active = true
            ORDER BY u.user_id, il.created_at ASC
        )
        SELECT 
            COALESCE(rk.type, 'other') as source_type,
            COUNT(*) as new_users
        FROM first_start fs
        LEFT JOIN ref_keys rk ON fs.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.type, 'other')
        ORDER BY new_users DESC
        """
        return await self._fetch_all(query)


    async def get_sales_by_type_month(self) -> List[Dict[str, Any]]:
        """
        Аналитика продаж по ТИПАМ источников за текущий месяц
        """
        query = """
        WITH user_first_order AS (
            SELECT DISTINCT ON (o.user_id)
                o.user_id,
                o.paid_at,
                p.amount_rub
            FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            WHERE o.status = 'paid'
            AND p.status = 'succeeded'
            AND DATE_TRUNC('month', o.paid_at) = DATE_TRUNC('month', CURRENT_DATE)
            ORDER BY o.user_id, o.paid_at ASC
        ),
        user_ref AS (
            SELECT DISTINCT ON (u.user_id)
                u.user_id,
                SUBSTRING(il.data->>'text' FROM 'ref_([a-zA-Z0-9_]+)') as ref_key
            FROM users u
            LEFT JOIN interaction_logs il ON u.user_id = il.user_id
                AND il.event_type = 'received'
                AND il.data->>'text' LIKE '%/start ref_%'
            ORDER BY u.user_id, il.created_at ASC
        )
        SELECT 
            COALESCE(rk.type, 'other') as source_type,
            COUNT(DISTINCT ufo.user_id) as unique_users,
            COUNT(*) as orders_count,
            COALESCE(SUM(ufo.amount_rub), 0) as total_amount
        FROM user_first_order ufo
        LEFT JOIN user_ref ur ON ufo.user_id = ur.user_id
        LEFT JOIN ref_keys rk ON ur.ref_key = rk.ref_key
        GROUP BY COALESCE(rk.type, 'other')
        ORDER BY total_amount DESC
        """
        return await self._fetch_all(query)        