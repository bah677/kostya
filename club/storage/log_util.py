"""Форматирование исключений в storage-слое (без зависимости от bot)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional


def format_exc(exc: BaseException) -> str:
    msg = str(exc).strip()
    if msg:
        return msg
    return type(exc).__name__


def is_transient_db_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, asyncio.CancelledError):
        return True
    low = format_exc(exc).lower()
    return "timeout" in low or "cancelled" in low


def log_storage_failure(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
    *,
    exc_info: bool = False,
) -> None:
    detail = format_exc(exc)
    if is_transient_db_error(exc):
        logger.warning("%s: %s (transient DB)", message, detail)
        return
    logger.error("%s: %s", message, detail, exc_info=exc_info)
