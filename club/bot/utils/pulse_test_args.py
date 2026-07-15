"""Разбор аргументов /scripture_pulse_test (цель, час слота, nopersist)."""

from __future__ import annotations

from typing import Optional, Tuple

from bot.texts.ru_targets import (
    DM_TARGET_WORDS,
    GROUP_TARGET_WORDS,
    SendTarget,
)

PulseSendTarget = SendTarget


def parse_pulse_test_args(
    args: Optional[str],
    *,
    default_slot_hour: int,
) -> Tuple[Optional[PulseSendTarget], int, bool]:
    """
    Возвращает (куда отправить, час слота, сохранять ли last_run после группы).

    Примеры: ``группа 12``, ``личка``, ``12 группа``, ``группа nopersist``.
    """
    target: Optional[PulseSendTarget] = None
    slot_hour: Optional[int] = None
    persist_state = True

    for token in (args or "").strip().lower().split():
        if token == "nopersist":
            persist_state = False
            continue
        if token.isdigit():
            slot_hour = int(token)
            continue
        if token in GROUP_TARGET_WORDS:
            target = "group"
        elif token in DM_TARGET_WORDS:
            target = "dm"

    return target, slot_hour if slot_hour is not None else default_slot_hour, persist_state
