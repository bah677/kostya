# storage/followup_storage.py
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
from storage.log_util import log_storage_failure

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class FollowupStorage:
    """Хранилище для работы с дожимом."""

    # Финальные статусы
    STATUS_FINAL_PAID = 901         # успешно оплатил
    STATUS_FINAL_SENSITIVE = 997    # тяжёлая тема — без дожима (фаза 1)
    STATUS_FINAL_REFUSED = 998      # явный отказ («нет», «стоп»)
    STATUS_FINAL_BLOCKED = 999      # заблокировал бота
    STATUS_ENGAGED_DONE = 112       # пинг engaged отправлен
    STATUS_WAITING_STUCK = 120        # ожидание пинга «застряли в диалоге»
    STATUS_STUCK_PING_SENT = 121      # пинг отправлен, ждём кнопку
    STATUS_STUCK_DONE = 122           # ответ + CTA выданы
    STATUS_TERMINAL = (901, 997, 998, 999, 203, 112, 122)
    
    def __init__(self, db):
        """`db` — UserStorage / Database (не кэшировать pool: он пересоздаётся при reconnect)."""
        self._db = db

    @property
    def pool(self):
        pool = self._db.pool
        if pool is None:
            raise RuntimeError("Database not connected")
        return pool
    
    # ==================== СТАТУСЫ ====================
    
    async def get_followup_state(self, user_id: int) -> Dict[str, Any]:
        """Получает статус дожима пользователя"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT status, started_at, updated_at, segment, last_topic,
                           stuck_context, last_assistant_at
                    FROM followup_states
                    WHERE user_id = $1
                """, user_id)
                
                if row:
                    sc = row["stuck_context"]
                    if isinstance(sc, str):
                        try:
                            sc = json.loads(sc)
                        except json.JSONDecodeError:
                            sc = None
                    return {
                        'status': row['status'],
                        'started_at': row['started_at'],
                        'updated_at': row['updated_at'],
                        'segment': row['segment'],
                        'last_topic': row['last_topic'],
                        'stuck_context': sc,
                        'last_assistant_at': row['last_assistant_at'],
                    }
                return {
                    'status': 0,
                    'started_at': None,
                    'updated_at': None,
                    'segment': None,
                    'last_topic': None,
                    'stuck_context': None,
                    'last_assistant_at': None,
                }
                
        except Exception as e:
            logger.error(f"❌ Failed to get followup state for user {user_id}: {e}")
            return {'status': 0, 'started_at': None, 'updated_at': None}
    
    async def set_followup_state(
        self,
        user_id: int,
        status: int,
        started_at: Optional[datetime] = None,
        *,
        segment: Optional[str] = None,
        last_topic: Optional[str] = None,
        reset_timer: bool = True,
        stuck_context: Optional[Dict[str, Any]] = None,
        last_assistant_at: Optional[datetime] = None,
    ) -> bool:
        """Устанавливает статус дожима (записи не удаляем)."""
        try:
            async with self.pool.acquire() as conn:
                if started_at is None and reset_timer:
                    started_at = _utc_now()
                else:
                    started_at = _as_utc(started_at)
                last_assistant_at = _as_utc(last_assistant_at)

                terminal = (901, 997, 998, 999, 203, 112, 122)
                sc_json = json.dumps(stuck_context) if stuck_context is not None else None
                await conn.execute(
                    """
                    INSERT INTO followup_states (
                        user_id, status, started_at, updated_at, segment, last_topic,
                        stuck_context, last_assistant_at
                    )
                    VALUES ($1, $2, $3, NOW(), $4, $5, $6::jsonb, $7)
                    ON CONFLICT (user_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        started_at = CASE
                            WHEN EXCLUDED.status = ANY($8::int[]) THEN followup_states.started_at
                            WHEN $9 THEN EXCLUDED.started_at
                            ELSE followup_states.started_at
                        END,
                        segment = COALESCE(EXCLUDED.segment, followup_states.segment),
                        last_topic = COALESCE(EXCLUDED.last_topic, followup_states.last_topic),
                        stuck_context = COALESCE(EXCLUDED.stuck_context, followup_states.stuck_context),
                        last_assistant_at = COALESCE(
                            EXCLUDED.last_assistant_at, followup_states.last_assistant_at
                        ),
                        updated_at = NOW()
                    """,
                    user_id,
                    status,
                    started_at,
                    segment,
                    last_topic,
                    sc_json,
                    last_assistant_at,
                    list(terminal),
                    reset_timer,
                )

                logger.info(
                    "Followup state user=%s status=%s segment=%s",
                    user_id,
                    status,
                    segment,
                )
                return True
                    
        except Exception as e:
            from storage.log_util import log_storage_failure

            log_storage_failure(logger, f"❌ Failed to set followup state for user {user_id}", e)
            return False
    
    async def get_users_by_status(self, status: int) -> List[Dict[str, Any]]:
        """Получает всех пользователей с указанным статусом (только активные)"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT fs.user_id, fs.status, fs.started_at, fs.segment, fs.last_topic,
                           fs.stuck_context, fs.last_assistant_at, u.is_active
                    FROM followup_states fs
                    JOIN users u ON fs.user_id = u.user_id
                    WHERE fs.status = $1
                      AND u.is_active = TRUE
                      AND fs.status NOT IN (901, 997, 998, 999, 203, 112, 122)
                    ORDER BY fs.started_at ASC
                """, status)
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            log_storage_failure(
                logger,
                f"❌ Failed to get users by status {status}",
                e,
            )
            return []


    async def is_active_followup(self, user_id: int) -> bool:
        """Проверяет, находится ли пользователь в активном дожиме"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval("""
                    SELECT status FROM followup_states
                    WHERE user_id = $1
                      AND status NOT IN (901, 997, 998, 999, 203, 112)
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
                            WHEN $1 IN (901, 997, 998, 999, 203, 112, 122) THEN started_at
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

    async def count_meaningful_private_user_messages(
        self, user_id: int, hours: int = 24
    ) -> int:
        """Сообщения пользователя в личке (не /start, не пустые) за последние N часов."""
        try:
            async with self.pool.acquire() as conn:
                return int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM messages
                        WHERE user_id = $1
                          AND chat_type = 'private'
                          AND role = 'user'
                          AND deleted_at IS NULL
                          AND COALESCE(message_type, '') <> 'callback'
                          AND content IS NOT NULL
                          AND TRIM(content) <> ''
                          AND content NOT ILIKE '/start%'
                          AND LENGTH(TRIM(content)) > 2
                          AND created_at > NOW() - ($2::int * INTERVAL '1 hour')
                        """,
                        user_id,
                        hours,
                    )
                    or 0
                )
        except Exception as e:
            logger.error(
                "Failed to count meaningful messages for user %s: %s", user_id, e
            )
            return 0

    async def user_has_refusal_in_private_chat(self, user_id: int) -> bool:
        """Явный отказ в последних репликах пользователя."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM messages
                        WHERE user_id = $1
                          AND chat_type = 'private'
                          AND role = 'user'
                          AND deleted_at IS NULL
                          AND created_at > NOW() - INTERVAL '90 days'
                          AND (
                            content ~* '(^|\\s)(нет|стоп)(\\s|$|!)'
                            OR content ILIKE '%не интерес%'
                            OR content ILIKE '%отстан%'
                            OR content ILIKE '%пока нет%'
                          )
                    )
                    """,
                    user_id,
                )
                return bool(row)
        except Exception as e:
            logger.error("Failed refusal check for user %s: %s", user_id, e)
            return False

    async def count_meaningful_private_user_messages_total(self, user_id: int) -> int:
        try:
            async with self.pool.acquire() as conn:
                return int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM messages
                        WHERE user_id = $1
                          AND chat_type = 'private'
                          AND role = 'user'
                          AND deleted_at IS NULL
                          AND COALESCE(message_type, '') <> 'callback'
                          AND content IS NOT NULL
                          AND TRIM(content) <> ''
                          AND content NOT ILIKE '/start%'
                          AND LENGTH(TRIM(content)) > 2
                        """,
                        user_id,
                    )
                    or 0
                )
        except Exception as e:
            logger.error("count meaningful total user %s: %s", user_id, e)
            return 0

    async def count_assistant_private_messages(self, user_id: int) -> int:
        try:
            async with self.pool.acquire() as conn:
                return int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM messages
                        WHERE user_id = $1
                          AND chat_type = 'private'
                          AND role = 'assistant'
                          AND deleted_at IS NULL
                        """,
                        user_id,
                    )
                    or 0
                )
        except Exception as e:
            logger.error("count assistant user %s: %s", user_id, e)
            return 0

    async def user_first_start_is_ref(self, user_id: int) -> bool:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval(
                    """
                    SELECT il.data->>'text'
                    FROM interaction_logs il
                    WHERE il.user_id = $1
                      AND COALESCE(il.data->>'text', '') ILIKE '/start ref_%'
                    ORDER BY il.created_at ASC
                    LIMIT 1
                    """,
                    user_id,
                )
                return bool(row)
        except Exception as e:
            logger.error("first start ref user %s: %s", user_id, e)
            return False

    async def get_last_meaningful_user_message_text(self, user_id: int) -> Optional[str]:
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval(
                    """
                    SELECT content
                    FROM messages
                    WHERE user_id = $1
                      AND chat_type = 'private'
                      AND role = 'user'
                      AND deleted_at IS NULL
                      AND content IS NOT NULL
                      AND TRIM(content) <> ''
                      AND content NOT ILIKE '/start%'
                      AND LENGTH(TRIM(content)) > 2
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.error("last user msg user %s: %s", user_id, e)
            return None

    async def user_private_texts_indicate_sensitive(self, user_id: int) -> bool:
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT content
                    FROM messages
                    WHERE user_id = $1
                      AND chat_type = 'private'
                      AND role = 'user'
                      AND deleted_at IS NULL
                      AND content IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 40
                    """,
                    user_id,
                )
                from bot.followup_segments import texts_indicate_sensitive

                return texts_indicate_sensitive([r["content"] for r in rows])
        except Exception as e:
            logger.error("sensitive check user %s: %s", user_id, e)
            return False

    async def update_segment_meta(
        self,
        user_id: int,
        *,
        segment: Optional[str] = None,
        last_topic: Optional[str] = None,
    ) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE followup_states
                    SET segment = COALESCE($2, segment),
                        last_topic = COALESCE($3, last_topic),
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    segment,
                    last_topic,
                )
        except Exception as e:
            logger.error("update_segment_meta user %s: %s", user_id, e)

    async def gather_segment_signals(self, user_id: int) -> Dict[str, Any]:
        return {
            "refusal": await self.user_has_refusal_in_private_chat(user_id),
            "sensitive": await self.user_private_texts_indicate_sensitive(user_id),
            "unpaid_order": await self.has_unpaid_order(user_id),
            "meaningful_count": await self.count_meaningful_private_user_messages_total(
                user_id
            ),
            "assistant_count": await self.count_assistant_private_messages(user_id),
            "ref_start": await self.user_first_start_is_ref(user_id),
        }

    async def get_last_private_message_meta(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Последнее сообщение в личке (user или assistant)."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT role, created_at
                    FROM messages
                    WHERE user_id = $1
                      AND chat_type = 'private'
                      AND deleted_at IS NULL
                      AND COALESCE(message_type, '') <> 'callback'
                      AND content IS NOT NULL
                      AND TRIM(content) <> ''
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("last_private_message_meta user %s: %s", user_id, e)
            return None

    async def user_wrote_after(self, user_id: int, since: datetime) -> bool:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM messages
                        WHERE user_id = $1
                          AND chat_type = 'private'
                          AND role = 'user'
                          AND deleted_at IS NULL
                          AND COALESCE(message_type, '') <> 'callback'
                          AND content IS NOT NULL
                          AND TRIM(content) <> ''
                          AND content NOT ILIKE '/start%'
                          AND LENGTH(TRIM(content)) > 2
                          AND created_at > $2
                    )
                    """,
                    user_id,
                    since,
                )
                return bool(row)
        except Exception as e:
            logger.error("user_wrote_after user %s: %s", user_id, e)
            return False

    async def set_stuck_context(
        self, user_id: int, context: Optional[Dict[str, Any]]
    ) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE followup_states
                    SET stuck_context = $2::jsonb, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    json.dumps(context) if context is not None else None,
                )
        except Exception as e:
            logger.error("set_stuck_context user %s: %s", user_id, e)

    async def log_stuck_event(
        self,
        user_id: int,
        event: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO interaction_logs (user_id, event_category, event_type, data)
                    VALUES ($1, 'followup', $2, $3::jsonb)
                    """,
                    user_id,
                    event,
                    json.dumps(extra or {}, ensure_ascii=False),
                )
        except Exception as e:
            logger.error("log_stuck_event user %s %s: %s", user_id, event, e)

    async def get_users_for_evening_followup(self) -> List[Dict[str, Any]]:
        """Статус 102 > 24 ч, только сегменты холодного ref/organic."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT fs.user_id, fs.status, fs.started_at, fs.segment
                    FROM followup_states fs
                    JOIN users u ON fs.user_id = u.user_id
                    WHERE fs.status = 102
                    AND fs.started_at < NOW() - INTERVAL '24 hours'
                    AND u.is_active = TRUE
                    AND COALESCE(fs.segment, 'organic_cold') IN ('ref_cold', 'organic_cold')
                    ORDER BY fs.started_at ASC
                """)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get users for evening followup: {e}")
            return []            