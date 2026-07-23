# storage/mailing_storage.py
import logging
import json
from typing import Any, Dict, List, Optional

from storage.db.database import Database

logger = logging.getLogger(__name__)

CAMPAIGN_SOURCE_MANUAL = "manual"
CAMPAIGN_SOURCE_SCHEDULED_MAILING_DAILY = "scheduled_mailing_daily"
CAMPAIGN_SOURCE_SCRIPTURE_ENCOURAGEMENT = "scripture_encouragement"

# --- Пороги восстановления после сбоя процесса или «молчания» воркера (в минутах).
# В рантайме задаются здесь; при необходимости позже можно перенести в mailing_settings.

# Сколько минут строка аудитории может оставаться в статусе ``processing`` без обновления
# ``updated_at`` / ``claimed_at`` (см. ``touch_processing_lease`` при отправке), прежде чем
# recover вернёт её в ``pending``. Нужна, чтобы после падения бота посреди цикла отправки
# получатель не терялся навсегда и чтобы второй инстанс не дублировал сообщение тем же user.
DEFAULT_PROCESSING_STALE_MINUTES = 15

# Сколько минут кампания может быть в статусе ``running`` без обновления
# ``mailing_campaigns.updated_at`` (heartbeat после каждого батча в ``MailingFeature``), прежде чем
# recover сочтёт воркер мёртвым: все ``processing`` этой кампании → ``pending``, кампания → ``planned``
# (снова попадёт в ``get_ready_campaigns``). Отдельно от ``DEFAULT_PROCESSING_STALE_MINUTES``:
# ловит ситуацию «процесс умер сразу после старта кампании», когда строки ещё «свежие».
DEFAULT_CAMPAIGN_STUCK_MINUTES = 60


class MailingStorage:
    """Хранилище для работы с рассылками"""

    def __init__(self, db: Database):
        self.db = db

    async def close(self) -> None:
        """Пул БД общий с UserStorage; закрывает только локальное (сейчас нечего)."""
        pass

    # ==================== КАМПАНИИ ====================

    async def create_campaign(self, campaign_data: Dict[str, Any]) -> Optional[int]:
        """Создаёт новую рассылку"""
        try:
            async with self.db.get_connection() as conn:
                campaign_source = campaign_data.get("campaign_source") or CAMPAIGN_SOURCE_MANUAL
                campaign_id = await conn.fetchval(
                    """
                    INSERT INTO mailing_campaigns (
                        name, text, parse_mode, scheduled_at, has_ref_link,
                        media_type, media_file_id, created_by, buttons,
                        campaign_source, attachments
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                    RETURNING id
                    """,
                    campaign_data["name"],
                    campaign_data["text"],
                    campaign_data.get("parse_mode", "HTML"),
                    campaign_data["scheduled_at"],
                    campaign_data.get("has_ref_link", False),
                    campaign_data.get("media_type"),
                    campaign_data.get("media_file_id"),
                    campaign_data.get("created_by"),
                    json.dumps(campaign_data.get("buttons", [])),
                    campaign_source,
                    json.dumps(campaign_data.get("attachments"))
                    if campaign_data.get("attachments") is not None
                    else None,
                )
                return campaign_id
        except Exception as e:
            logger.error(f"❌ Failed to create campaign: {e}")
            return None

    async def get_campaign(self, campaign_id: int) -> Optional[Dict[str, Any]]:
        """Получает кампанию по ID"""
        try:
            async with self.db.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM mailing_campaigns WHERE id = $1", campaign_id
                )
                if row:
                    campaign = dict(row)
                    if campaign.get("buttons"):
                        if isinstance(campaign["buttons"], str):
                            campaign["buttons"] = json.loads(campaign["buttons"])
                    else:
                        campaign["buttons"] = []
                    att = campaign.get("attachments")
                    if isinstance(att, str):
                        campaign["attachments"] = json.loads(att)
                    elif att is None:
                        campaign["attachments"] = None
                    elif isinstance(att, list):
                        campaign["attachments"] = att
                    else:
                        campaign["attachments"] = list(att) if att else []
                    return campaign
                return None
        except Exception as e:
            logger.error(f"❌ Failed to get campaign {campaign_id}: {e}")
            return None

    async def update_campaign_scheduled_at(
        self, campaign_id: int, scheduled_at
    ) -> bool:
        """Сдвигает ``scheduled_at`` (например, approve черновика → now)."""
        try:
            async with self.db.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE mailing_campaigns
                       SET scheduled_at = $2,
                           updated_at = NOW()
                     WHERE id = $1
                    """,
                    campaign_id,
                    scheduled_at,
                )
                return True
        except Exception as e:
            logger.error("❌ Failed to update campaign scheduled_at %s: %s", campaign_id, e)
            return False

    async def update_campaign_status(
        self,
        campaign_id: int,
        status: str,
        sent_count: int = None,
        failed_count: int = None,
        blocked_count: int = None,
    ) -> bool:
        """Обновляет статус и статистику кампании"""
        try:
            async with self.db.get_connection() as conn:
                updates = []
                params = []
                idx = 1

                updates.append(f"status = ${idx}::varchar")
                params.append(status)
                idx += 1

                if sent_count is not None:
                    updates.append(f"sent_count = ${idx}")
                    params.append(sent_count)
                    idx += 1
                if failed_count is not None:
                    updates.append(f"failed_count = ${idx}")
                    params.append(failed_count)
                    idx += 1
                if blocked_count is not None:
                    updates.append(f"blocked_count = ${idx}")
                    params.append(blocked_count)
                    idx += 1

                updates.append("updated_at = NOW()")
                params.append(campaign_id)

                await conn.execute(
                    f"""
                    UPDATE mailing_campaigns
                    SET {", ".join(updates)}
                    WHERE id = ${idx}
                    """,
                    *params,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update campaign status: {e}")
            return False

    async def get_ready_campaigns(self) -> List[Dict[str, Any]]:
        """Получает кампании, готовые к запуску"""
        try:
            async with self.db.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM mailing_campaigns
                    WHERE status = 'planned' AND scheduled_at <= NOW()
                    ORDER BY scheduled_at ASC
                    """
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get ready campaigns: {e}")
            return []

    async def recover_stale_mailing_state(
        self,
        *,
        processing_stale_minutes: int = DEFAULT_PROCESSING_STALE_MINUTES,
        campaign_stuck_minutes: int = DEFAULT_CAMPAIGN_STUCK_MINUTES,
    ) -> None:
        """
        После падения процесса / зависания:
        1) «processing» без прогресса дольше порога → снова «pending»;
        2) кампании «running» без heartbeat кампании дольше порога → всю «processing»
           этой кампании в «pending», кампания → «planned» (повторный воркер подхватит).
        """
        try:
            async with self.db.get_connection() as conn:
                stale_proc = await conn.execute(
                    """
                    UPDATE mailing_audience
                       SET status = 'pending',
                           error = CASE
                               WHEN COALESCE(TRIM(error), '') = ''
                               THEN '[auto] stale processing → pending'
                               ELSE error || ' | [auto] stale processing → pending'
                           END,
                           claimed_at = NULL,
                           updated_at = NOW()
                     WHERE status = 'processing'
                       AND updated_at < NOW() - $1::bigint * INTERVAL '1 minute'
                    """,
                    processing_stale_minutes,
                )

                stale_n = 0
                if isinstance(stale_proc, str) and stale_proc.startswith("UPDATE "):
                    try:
                        stale_n = int(stale_proc.split()[1])
                    except (ValueError, IndexError):
                        pass

                ghost_rows = await conn.fetch(
                    """
                    SELECT id FROM mailing_campaigns
                     WHERE status = 'running'
                       AND updated_at < NOW() - $1::bigint * INTERVAL '1 minute'
                    """,
                    campaign_stuck_minutes,
                )
                cid_list = [r["id"] for r in ghost_rows]
                for cid in cid_list:
                    await conn.execute(
                        """
                        UPDATE mailing_audience
                           SET status = 'pending',
                               error = CASE
                                   WHEN COALESCE(TRIM(error), '') = ''
                                   THEN '[auto] ghost running campaign reset'
                                   ELSE error || ' | [auto] ghost running campaign reset'
                               END,
                               claimed_at = NULL,
                               updated_at = NOW()
                         WHERE campaign_id = $1
                           AND status = 'processing'
                        """,
                        cid,
                    )
                    await conn.execute(
                        """
                        UPDATE mailing_campaigns
                           SET status = 'planned',
                               updated_at = NOW()
                         WHERE id = $1 AND status = 'running'
                        """,
                        cid,
                    )
                    logger.warning(
                        "🔄 Ghost mailing campaign=%s reset to planned (heartbeat lost)",
                        cid,
                    )

                if stale_n or cid_list:
                    logger.warning(
                        "🔄 mailing recovery: stale_processing_rows=%s, ghost_campaigns=%s",
                        stale_n,
                        len(cid_list),
                    )
        except Exception as e:
            logger.error(f"❌ Failed to recover stale mailing state: {e}", exc_info=True)

    # Alias для ясности в коде вызывающей стороны
    async def reset_stuck_campaigns(self) -> int:
        await self.recover_stale_mailing_state()
        return 0

    # ==================== АУДИТОРИЯ (ЛОГ ОТПРАВКИ) ====================

    async def bump_user_mailing_after_send(self, user_id: int, *, sent: bool = True) -> bool:
        """Обновляет last_mailing_at / mailing_count после успешной рассылки (как было в scheduled_mailing)."""
        try:
            async with self.db.get_connection() as conn:
                if sent:
                    await conn.execute(
                        """
                        UPDATE users
                           SET last_mailing_at = NOW(),
                               mailing_count = mailing_count + 1
                         WHERE user_id = $1
                        """,
                        user_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE users SET is_active = false WHERE user_id = $1
                        """,
                        user_id,
                    )
                return True
        except Exception as e:
            logger.error("❌ bump_user_mailing_after_send uid=%s: %s", user_id, e)
            return False

    async def add_audience_batch(self, campaign_id: int, user_ids: List[int]) -> int:
        """Добавляет batch пользователей в аудиторию рассылки"""
        try:
            async with self.db.get_connection() as conn:
                added = 0
                for user_id in user_ids:
                    result = await conn.execute(
                        """
                        INSERT INTO mailing_audience (campaign_id, user_id)
                        VALUES ($1, $2)
                        ON CONFLICT (campaign_id, user_id) DO NOTHING
                        """,
                        campaign_id,
                        user_id,
                    )
                    if result and "INSERT 0 1" in result:
                        added += 1
                return added
        except Exception as e:
            logger.error(f"❌ Failed to add audience batch: {e}")
            return 0

    async def get_audience_count(self, campaign_id: int, status: str = None) -> int:
        """Получает количество пользователей в аудитории"""
        try:
            async with self.db.get_connection() as conn:
                if status:
                    return await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM mailing_audience
                        WHERE campaign_id = $1 AND status = $2
                        """,
                        campaign_id,
                        status,
                    )
                else:
                    return await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM mailing_audience
                        WHERE campaign_id = $1
                        """,
                        campaign_id,
                    )
        except Exception as e:
            logger.error(f"❌ Failed to get audience count: {e}")
            return 0

    async def count_open_audience(self, campaign_id: int) -> int:
        """Строки, по которым ещё нужна отправка (pending или в работе worker-а)."""
        try:
            async with self.db.get_connection() as conn:
                return await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM mailing_audience
                    WHERE campaign_id = $1
                      AND status IN ('pending', 'processing')
                    """,
                    campaign_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to count open mailing audience: {e}")
            return 0

    async def claim_audience_batch(
        self, campaign_id: int, limit: int
    ) -> List[Dict[str, Any]]:
        """
        Атомарно переводит до limit строк pending → processing и возвращает их.
        Один источник правды против дублей при нескольких инстансах бота (блокировки на время UPDATE).
        """
        try:
            async with self.db.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE mailing_audience AS ma
                       SET status = 'processing',
                           updated_at = NOW(),
                           claimed_at = NOW()
                      FROM (
                          SELECT id FROM mailing_audience
                           WHERE campaign_id = $1
                             AND status = 'pending'
                           ORDER BY id
                           LIMIT $2
                           FOR UPDATE SKIP LOCKED
                      ) AS sub
                     WHERE ma.id = sub.id
                    RETURNING ma.id, ma.user_id
                    """,
                    campaign_id,
                    limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to claim audience batch: {e}")
            return []

    async def touch_processing_lease(self, audience_id: int) -> bool:
        """
        Обновляет метку активности строки «processing», чтобы долгая отправка /
        паузы по flood_wait не воспринимались как зависшие.
        """
        try:
            async with self.db.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE mailing_audience
                       SET updated_at = NOW(),
                           claimed_at = NOW()
                     WHERE id = $1 AND status = 'processing'
                    """,
                    audience_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to touch_processing_lease id=%s: %s", audience_id, e)
            return False

    async def update_audience_status(
        self,
        audience_id: int,
        status: str,
        error: str = None,
        attempt_count: int = None,
    ) -> bool:
        """Обновляет статус отправки для пользователя"""
        try:
            async with self.db.get_connection() as conn:
                if attempt_count is not None:
                    await conn.execute(
                        """
                        UPDATE mailing_audience
                           SET status = $1::varchar,
                               error = $2,
                               attempt_count = $3,
                               sent_at = CASE WHEN $1 = 'sent' THEN NOW()
                                              ELSE sent_at END,
                               claimed_at = CASE WHEN $1::varchar IN ('sent', 'failed', 'blocked')
                                                    THEN NULL
                                                    ELSE claimed_at END,
                               updated_at = NOW()
                         WHERE id = $4
                        """,
                        status,
                        error,
                        attempt_count,
                        audience_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE mailing_audience
                           SET status = $1::varchar,
                               error = $2,
                               sent_at = CASE WHEN $1 = 'sent' THEN NOW()
                                              ELSE sent_at END,
                               claimed_at = CASE WHEN $1::varchar IN ('sent', 'failed', 'blocked')
                                                    THEN NULL
                                                    ELSE claimed_at END,
                               updated_at = NOW()
                         WHERE id = $3
                        """,
                        status,
                        error,
                        audience_id,
                    )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update audience status: {e}")
            return False

    # ==================== НАСТРОЙКИ СКОРОСТИ ====================

    async def get_mailing_settings(self) -> Dict[str, Any]:
        """Получает настройки скорости рассылок"""
        try:
            async with self.db.get_connection() as conn:
                row = await conn.fetchrow("SELECT * FROM mailing_settings WHERE id = 1")
                if row:
                    return dict(row)
                return {
                    "messages_per_second": 5,
                    "batch_size": 50,
                    "max_attempts": 3,
                    "min_rate": 2,
                    "max_rate": 8,
                }
        except Exception as e:
            logger.error(f"❌ Failed to get mailing settings: {e}")
            return {
                "messages_per_second": 5,
                "batch_size": 50,
                "max_attempts": 3,
                "min_rate": 2,
                "max_rate": 8,
            }

    async def deactivate_user(self, user_id: int) -> bool:
        """Деактивирует пользователя, который заблокировал бота"""
        try:
            async with self.db.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users SET is_active = FALSE
                    WHERE user_id = $1 AND is_active = TRUE
                    """,
                    user_id,
                )
                logger.info(f"🚫 User {user_id} deactivated")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to deactivate user {user_id}: {e}")
            return False

    # ==================== АДМИН-МАСТЕР /new_mailing ====================

    async def list_recent_campaigns_no_test(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Последние кампании для шага исключения (без «тест» и «(авто)» в названии)."""
        try:
            async with self.db.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, name, created_at, status
                    FROM mailing_campaigns
                    WHERE name NOT ILIKE '%тест%'
                      AND name NOT ILIKE '%(авто)%'
                    ORDER BY id DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ list_recent_campaigns_no_test: %s", e)
            return []

    async def get_audience_user_ids_for_campaigns(
        self, campaign_ids: List[int]
    ) -> List[int]:
        """Telegram user_id всех строк mailing_audience указанных кампаний."""
        if not campaign_ids:
            return []
        try:
            async with self.db.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT user_id
                    FROM mailing_audience
                    WHERE campaign_id = ANY($1::bigint[])
                    """,
                    campaign_ids,
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("❌ get_audience_user_ids_for_campaigns: %s", e)
            return []
