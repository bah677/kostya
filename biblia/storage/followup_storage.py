# storage/followup_storage.py
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class FollowupStorage:
    """Хранилище для работы с дожимом"""
    
    # Финальные статусы
    STATUS_FINAL_PAID = 901        # успешно оплатил
    STATUS_FINAL_BLOCKED = 999     # заблокировал бота
    
    def __init__(self, db_pool):
        self.pool = db_pool
    
    # ==================== СТАТУСЫ ====================
    
    async def get_followup_state(self, user_id: int) -> Dict[str, Any]:
        """Получает статус дожима пользователя"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT status, started_at, updated_at
                    FROM followup_states
                    WHERE user_id = $1
                """, user_id)
                
                if row:
                    return {
                        'status': row['status'],
                        'started_at': row['started_at'],
                        'updated_at': row['updated_at']
                    }
                return {'status': 0, 'started_at': None, 'updated_at': None}
                
        except Exception as e:
            logger.error(f"❌ Failed to get followup state for user {user_id}: {e}")
            return {'status': 0, 'started_at': None, 'updated_at': None}
    
    async def set_followup_state(self, user_id: int, status: int, started_at: Optional[datetime] = None) -> bool:
        """
        Устанавливает статус дожима
        Никогда не удаляем записи, только обновляем статус
        """
        try:
            async with self.pool.acquire() as conn:
                if started_at is None:
                    started_at = datetime.now()
                
                # 🔥 ИСПРАВЛЯЕМ: убираем ambiguity, явно указываем столбцы
                await conn.execute("""
                    INSERT INTO followup_states (user_id, status, started_at, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        started_at = CASE 
                            WHEN EXCLUDED.status IN (901, 999) THEN followup_states.started_at
                            ELSE EXCLUDED.started_at 
                        END,
                        updated_at = NOW()
                """, user_id, status, started_at)
                
                logger.info(f"✅ Followup state for user {user_id} set to {status}")
                return True
                    
        except Exception as e:
            logger.error(f"❌ Failed to set followup state for user {user_id}: {e}")
            return False
    
    async def get_users_by_status(self, status: int) -> List[Dict[str, Any]]:
        """Получает всех пользователей с указанным статусом (только активные)"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT fs.user_id, fs.status, fs.started_at, u.is_active
                    FROM followup_states fs
                    JOIN users u ON fs.user_id = u.user_id
                    WHERE fs.status = $1
                      AND u.is_active = TRUE
                      AND fs.status NOT IN (901, 999)
                    ORDER BY fs.started_at ASC
                """, status)
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Failed to get users by status {status}: {e}")
            return []


    async def is_active_followup(self, user_id: int) -> bool:
        """Проверяет, находится ли пользователь в активном дожиме"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval("""
                    SELECT status FROM followup_states
                    WHERE user_id = $1
                      AND status NOT IN (901, 999)
                """, user_id)
                
                return row is not None
                
        except Exception as e:
            logger.error(f"❌ Failed to check active followup for user {user_id}: {e}")
            return False        
    
    # ==================== СООБЩЕНИЯ ====================
    
    async def get_followup_message(self, status: int) -> Optional[Dict[str, Any]]:
        """Получает сообщение для статуса"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT id, message_text, delay_minutes
                    FROM followup_messages
                    WHERE status = $1 AND is_active = TRUE
                """, status)
                
                return dict(row) if row else None
                
        except Exception as e:
            logger.error(f"❌ Failed to get followup message for status {status}: {e}")
            return None
    
    async def get_all_messages(self) -> List[Dict[str, Any]]:
        """Получает все активные сообщения для кэша"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT status, message_text, delay_minutes
                    FROM followup_messages
                    WHERE is_active = TRUE
                """)
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Failed to get all followup messages: {e}")
            return []
    
    # ==================== ЛОГИ ====================
    
    async def log_send(self, user_id: int, status: int, message_id: int, 
                       delivered: bool = True, error: str = None) -> bool:
        """Логирует отправку сообщения"""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO followup_log (user_id, status, message_id, delivered, error)
                    VALUES ($1, $2, $3, $4, $5)
                """, user_id, status, message_id, delivered, error)
                
                logger.info(f"📝 Logged followup send: user={user_id}, status={status}, delivered={delivered}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to log followup send: {e}")
            return False
    
    async def has_recent_log(self, user_id: int, status: int, minutes: int = 60) -> bool:
        """Проверяет, был ли недавний лог отправки"""
        try:
            async with self.pool.acquire() as conn:
                # 🔥 ВЫЧИСЛЯЕМ ДАТУ В PYTHON
                cutoff_time = datetime.now() - timedelta(minutes=minutes)
                
                # 🔥 ИСПРАВЛЯЕМ: только 2 параметра
                row = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM followup_log
                        WHERE user_id = $1 
                          AND status = $2
                          AND sent_at > $3
                    )
                """, user_id, status, cutoff_time)
                
                return row
                
        except Exception as e:
            logger.error(f"❌ Failed to check recent log: {e}")
            return False
    
    # ==================== ПРОВЕРКИ ====================
    
    async def has_unpaid_order(self, user_id: int) -> bool:
        """Проверяет, есть ли у пользователя неоплаченный заказ"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM orders o
                        LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
                        WHERE o.user_id = $1
                          AND o.status = 'pending'
                          AND (p.id IS NULL OR p.status != 'succeeded')
                    )
                """, user_id)
                
                return row
                
        except Exception as e:
            logger.error(f"❌ Failed to check unpaid order for user {user_id}: {e}")
            return False
    
    async def has_paid_order(self, user_id: int) -> bool:
        """Проверяет, есть ли у пользователя оплаченный заказ"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM orders o
                        JOIN payments p ON o.id = p.order_id
                        WHERE o.user_id = $1
                          AND p.status = 'succeeded'
                    )
                """, user_id)
                
                return row
                
        except Exception as e:
            logger.error(f"❌ Failed to check paid order for user {user_id}: {e}")
            return False


    async def has_active_license(self, user_id: int) -> bool:
        """
        Проверяет, есть ли у пользователя активная лицензия
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM license 
                        WHERE user_id = $1 
                        AND status = 'active'
                        AND expires_at > NOW()
                    )
                """, user_id)
                return row
        except Exception as e:
            logger.error(f"❌ Failed to check active license for user {user_id}: {e}")
            return False        

    async def try_advance_status(self, user_id: int, current_status: int, next_status: int) -> bool:
        """
        Атомарно обновляет статус, только если он не изменился
        Возвращает True если обновление выполнено
        """
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE followup_states 
                    SET status = $1,
                        started_at = CASE 
                            WHEN $1 IN (901, 999) THEN started_at
                            ELSE NOW()
                        END,
                        updated_at = NOW()
                    WHERE user_id = $2 AND status = $3
                """, next_status, user_id, current_status)
                
                # result будет "UPDATE 1" если обновили, "UPDATE 0" если нет
                return result == "UPDATE 1"
                
        except Exception as e:
            logger.error(f"❌ Failed to advance status for user {user_id}: {e}")
            return False        

    async def get_users_for_evening_followup(self) -> List[Dict[str, Any]]:
        """Получает пользователей со статусом 102, у которых started_at > 24 часов"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT fs.user_id, fs.status, fs.started_at
                    FROM followup_states fs
                    JOIN users u ON fs.user_id = u.user_id
                    WHERE fs.status = 102
                    AND fs.started_at < NOW() - INTERVAL '24 hours'
                    AND u.is_active = TRUE
                    ORDER BY fs.started_at ASC
                """)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get users for evening followup: {e}")
            return []            