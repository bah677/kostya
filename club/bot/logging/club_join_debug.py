"""
Отдельный файл JSONL для отладки входа в клубную группу (chat_member vs message/new_chat_members).

Включение: ``CLUB_JOIN_DEBUG_LOG=/path/to/club_join_debug.jsonl`` в .env.
Строка — один JSON-объект на событие; удобно ``grep`` / ``jq``.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_enabled = False
_file_path: Optional[Path] = None


def configure(log_path: str) -> None:
    """Вызвать из ``initialize`` после загрузки конфига. Пустая строка — выключено."""
    global _enabled, _file_path
    p = (log_path or "").strip()
    if not p or p.lower() in ("0", "false", "off", "no"):
        _enabled = False
        _file_path = None
        return
    _file_path = Path(p).expanduser()
    if not _file_path.is_absolute():
        _file_path = Path.cwd() / _file_path
    _file_path.parent.mkdir(parents=True, exist_ok=True)
    _enabled = True
    logger.info("club_join_debug: пишем в %s", _file_path)


def log_event(kind: str, **fields: Any) -> None:
    """Добавить запись (ts_utc, kind, ...). Без настройки path — no-op."""
    if not _enabled or _file_path is None:
        return
    row: Dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **fields,
    }
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    try:
        with _lock:
            with open(_file_path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        logger.warning("club_join_debug: не удалось записать: %s", e)


def chat_member_status(cm: Any) -> Optional[str]:
    """Человекочитаемый статус из ChatMember."""
    if cm is None:
        return None
    st = getattr(cm, "status", None)
    if st is None:
        return None
    if hasattr(st, "value"):
        return str(st.value)
    return str(st)


def is_club_member_join_transition(old_cm: Any, new_cm: Any) -> bool:
    """
    True, если человек впервые оказывается «внутри» группы (после left/kicked/вне restricted).
    Нужно, когда Telegram не присылает service message с new_chat_members (скрытый список участников).
    """
    old_st = chat_member_status(old_cm)
    new_st = chat_member_status(new_cm)

    was_outside = old_st in (None, "left", "kicked") or (
        old_st == "restricted"
        and old_cm is not None
        and not getattr(old_cm, "is_member", False)
    )

    is_inside = new_st in ("member", "administrator", "creator") or (
        new_st == "restricted"
        and new_cm is not None
        and getattr(new_cm, "is_member", False)
    )

    return bool(was_outside and is_inside)
