"""Очередь TTS молитв: ограничивает параллельные генерации при нагрузке."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

OnQueued = Callable[[int], Awaitable[None]]


class PrayerTtsQueue:
    """FIFO-слоты на озвучку молитв: не больше ``max_concurrent`` одновременных синтезов."""

    def __init__(self, max_concurrent: int = 1) -> None:
        n = max(1, int(max_concurrent))
        self.max_concurrent = n
        self._sem = asyncio.Semaphore(n)
        self._lock = asyncio.Lock()
        self._waiting = 0
        self._running = 0

    def snapshot(self) -> tuple[int, int]:
        """(waiting, running)."""
        return self._waiting, self._running

    @property
    def load(self) -> int:
        return self._waiting + self._running

    def is_busy(self) -> bool:
        """Есть ли уже занятые слоты / очередь (новый запрос будет ждать)."""
        return self._running >= self.max_concurrent or self._waiting > 0

    @asynccontextmanager
    async def hold(
        self,
        *,
        label: str = "tts",
        on_queued: Optional[OnQueued] = None,
    ) -> AsyncIterator[None]:
        async with self._lock:
            self._waiting += 1
            # Сколько ещё впереди (другие ждущие + сейчас на GPU).
            ahead = (self._waiting - 1) + self._running
            waiting = self._waiting
            running = self._running
        logger.info(
            "prayer TTS queue enter label=%s waiting=%s running=%s ahead=%s max=%s",
            label,
            waiting,
            running,
            ahead,
            self.max_concurrent,
        )
        if ahead > 0 and on_queued is not None:
            try:
                await on_queued(ahead)
            except Exception as e:
                logger.debug("prayer TTS on_queued failed label=%s: %s", label, e)

        acquired = False
        try:
            await self._sem.acquire()
            acquired = True
            async with self._lock:
                self._waiting -= 1
                self._running += 1
            logger.info(
                "prayer TTS queue acquired label=%s running=%s",
                label,
                self._running,
            )
            yield
        finally:
            if acquired:
                async with self._lock:
                    self._running = max(0, self._running - 1)
                self._sem.release()
                logger.info(
                    "prayer TTS queue release label=%s running=%s waiting=%s",
                    label,
                    self._running,
                    self._waiting,
                )
            else:
                async with self._lock:
                    self._waiting = max(0, self._waiting - 1)


_QUEUE: Optional[PrayerTtsQueue] = None


def get_prayer_tts_queue(max_concurrent: int = 1) -> PrayerTtsQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = PrayerTtsQueue(max_concurrent=max_concurrent)
    return _QUEUE
