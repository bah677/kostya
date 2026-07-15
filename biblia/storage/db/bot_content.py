"""
Таблица ``bot_content``: кнопки /more (категория ``more_buttons``), как в legacy Biblia.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BotContentMixin:

    async def get_more_buttons(self) -> List[Dict[str, Any]]:
        """Активные кнопки для /more (порядок ``order_index``)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, key, button_text, command, content_text,
                           model, order_index, is_active
                      FROM bot_content
                     WHERE category = 'more_buttons' AND is_active = TRUE
                     ORDER BY order_index, id
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ get_more_buttons: %s", e, exc_info=True)
            return []

    async def get_button_by_id(self, button_id: int) -> Optional[Dict[str, Any]]:
        """Строка кнопки по id (для callback ``more_button_<id>``)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, key, button_text, command, content_text,
                           model, order_index, category, is_active, content_type
                      FROM bot_content
                     WHERE id = $1 AND is_active = TRUE
                    """,
                    button_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_button_by_id %s: %s", button_id, e, exc_info=True)
            return None
