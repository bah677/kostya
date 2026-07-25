"""Кольцевой буфер последних сообщений юзера в топике общения."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict, List, Tuple


@dataclass(frozen=True)
class TopicMsg:
    text: str
    message_id: int


class TopicAssistContextBuffer:
    def __init__(self, maxlen: int = 4) -> None:
        self._maxlen = max(1, int(maxlen))
        self._bufs: Dict[Tuple[int, int], Deque[TopicMsg]] = defaultdict(
            lambda: deque(maxlen=self._maxlen)
        )
        self._lock = Lock()

    def set_maxlen(self, maxlen: int) -> None:
        self._maxlen = max(1, int(maxlen))

    def push(self, chat_id: int, user_id: int, text: str, message_id: int) -> None:
        t = (text or "").strip()
        if not t:
            return
        key = (int(chat_id), int(user_id))
        with self._lock:
            buf = self._bufs[key]
            if buf.maxlen != self._maxlen:
                self._bufs[key] = deque(buf, maxlen=self._maxlen)
                buf = self._bufs[key]
            buf.append(TopicMsg(text=t[:2000], message_id=int(message_id)))

    def tail(self, chat_id: int, user_id: int) -> List[TopicMsg]:
        key = (int(chat_id), int(user_id))
        with self._lock:
            return list(self._bufs.get(key) or [])

    def format_tail(self, chat_id: int, user_id: int) -> str:
        items = self.tail(chat_id, user_id)
        if not items:
            return ""
        return "\n".join(f"- {m.text}" for m in items)
