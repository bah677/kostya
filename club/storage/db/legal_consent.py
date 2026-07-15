"""Согласие пользователя с юридическими документами (user_legal_consents)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _json_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False)


class LegalConsentMixin:
    async def has_user_legal_consent(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    "SELECT 1 FROM user_legal_consents WHERE user_id = $1 LIMIT 1",
                    user_id,
                )
                return val is not None
        except Exception as e:
            logger.error("has_user_legal_consent user=%s: %s", user_id, e)
            return False

    async def record_user_legal_consent(
        self,
        user_id: int,
        *,
        source: str,
        bot_variant: Optional[str] = None,
        telegram_user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
        is_premium: Optional[bool] = None,
        is_bot: Optional[bool] = None,
        chat_id: Optional[int] = None,
        chat_type: Optional[str] = None,
        message_id: Optional[int] = None,
        callback_query_id: Optional[str] = None,
        inline_message_id: Optional[str] = None,
        raw_user_json: Optional[Dict[str, Any]] = None,
        raw_chat_json: Optional[Dict[str, Any]] = None,
        consented_at: Optional[datetime] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO user_legal_consents (
                        user_id, consented_at, source, bot_variant,
                        telegram_user_id, username, first_name, last_name,
                        language_code, is_premium, is_bot,
                        chat_id, chat_type, message_id,
                        callback_query_id, inline_message_id,
                        raw_user_json, raw_chat_json
                    ) VALUES (
                        $1, COALESCE($2, NOW()), $3, $4,
                        $5, $6, $7, $8,
                        $9, $10, $11,
                        $12, $13, $14,
                        $15, $16,
                        $17::jsonb, $18::jsonb
                    )
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    user_id,
                    consented_at,
                    source,
                    bot_variant,
                    telegram_user_id,
                    username,
                    first_name,
                    last_name,
                    language_code,
                    is_premium,
                    is_bot,
                    chat_id,
                    chat_type,
                    message_id,
                    callback_query_id,
                    inline_message_id,
                    _json_or_none(raw_user_json),
                    _json_or_none(raw_chat_json),
                )
            return True
        except Exception as e:
            logger.error("record_user_legal_consent user=%s: %s", user_id, e)
            return False
