"""Распознавание ожидаемых ошибок Telegram API."""

from __future__ import annotations


def format_exception(exc: BaseException) -> str:
    msg = str(exc).strip()
    if msg:
        return msg
    return type(exc).__name__


def is_user_unreachable_error(exc: BaseException) -> bool:
    low = format_exception(exc).lower()
    return any(
        s in low
        for s in (
            "bot was blocked",
            "user is deactivated",
            "chat not found",
            "forbidden: bot was blocked",
        )
    )


def is_topic_closed_error(exc: BaseException) -> bool:
    return "TOPIC_CLOSED" in format_exception(exc).upper()
