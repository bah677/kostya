"""Шаги первой недели: когда слать, hint для composer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class FirstWeekStepPlan:
    step: int
    channel: str  # group | dm
    goal: str
    composer_hint: str
    skip_reason: Optional[str] = None


def _local_now(tz_name: Optional[str] = None) -> datetime:
    try:
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
    except Exception:
        pass
    return datetime.now(MSK)


def in_send_window(now: Optional[datetime] = None, *, tz_name: Optional[str] = None) -> bool:
    """10:00–20:00 локально (или МСК)."""
    now = now or _local_now(tz_name)
    return 10 <= now.hour < 20


def next_window_start(now: Optional[datetime] = None, *, tz_name: Optional[str] = None) -> datetime:
    now = now or _local_now(tz_name)
    if now.hour < 10:
        return now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now.hour >= 20:
        nxt = now + timedelta(days=1)
        return nxt.replace(hour=10, minute=0, second=0, microsecond=0)
    return now


def resolve_due_step(
    fw: dict,
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> Optional[FirstWeekStepPlan]:
    """
    Какой шаг созрел сейчас.
    step в таблице = последний ВЫПОЛНЕННЫЙ (после входа step=1 = приветствие в группе сделано).
    Следующий к отправке = step+1, кроме старта: при создании step=1 уже означает «приветствие ушло».
    """
    if fw.get("ended_at"):
        return None
    now = now or _local_now(tz_name)
    started = fw["started_at"]
    if started.tzinfo is None:
        started = started.replace(tzinfo=MSK)
    started = started.astimezone(now.tzinfo)

    msgs = int(fw.get("msgs_group") or 0)
    done = fw.get("done_at") is not None
    last_done_step = int(fw.get("step") or 0)
    # следующий кандидат
    next_step = last_done_step + 1
    if next_step > 6:
        return None

    hours = (now - started).total_seconds() / 3600.0
    days = (now.date() - started.astimezone(MSK).date()).days

    def _hint_step2() -> str:
        return (
            "Первая неделя, шаг 2. Человек только вошёл и ещё не писал в группу. "
            "Коротко расскажи, что сейчас живого в группе: одна тема и одна ссылка "
            "на конкретное сообщение. Без упрёков. Вопрос — чтобы ответили в группе, не тебе."
        )

    def _hint_step3() -> str:
        return (
            "Первая неделя, шаг 3 — дайджест. 2–3 темы за сутки, каждая со ссылкой "
            "на сообщение в группе. Тон тёплый. Без оплаты. Вопрос — в группу."
        )

    def _hint_step4() -> str:
        return (
            "Первая неделя, шаг 4. Расскажи про ближайший эфир: когда, о чём, зачем прийти. "
            "Если эфира нет — предложи запись последнего. Одна ссылка. Без продажи."
        )

    def _hint_step5() -> str:
        return (
            "Первая неделя, шаг 5. Человек почти не писал в группе. 2–3 предложения. "
            "Опирайся на живую тему в группе. Не упрекай за молчание. "
            "Закончи вопросом, ответ на который логично написать в группу. Без markdown-продажи."
        )

    def _hint_step6() -> str:
        return (
            "Первая неделя, шаг 6 — итог недели. Что человек мог застать, что будет дальше. "
            "Без цены, продления и кнопок оплаты. Одна мягкая ссылка в группу."
        )

    # step 1 = welcome in group — already sent at join; nothing for proactive
    if next_step == 2:
        if msgs >= 1 or done:
            return FirstWeekStepPlan(
                2, "dm", "first_week", "", skip_reason="already_wrote_group"
            )
        if hours < 2:
            return None
        if not in_send_window(now, tz_name=tz_name):
            return None
        return FirstWeekStepPlan(2, "dm", "first_week", _hint_step2())

    if next_step == 3:
        if msgs >= 3 or done:
            return FirstWeekStepPlan(
                3, "dm", "first_week", "", skip_reason="msgs_group_ge_3"
            )
        if days < 2 and hours < 36:
            # «день 2»: не раньше следующего календарного дня после started, и не раньше ~24ч
            if days < 1 or hours < 20:
                return None
        if not in_send_window(now, tz_name=tz_name):
            return None
        return FirstWeekStepPlan(3, "dm", "first_week", _hint_step3())

    if next_step == 4:
        if days < 3 and hours < 48:
            if days < 2 or hours < 44:
                return None
        if not in_send_window(now, tz_name=tz_name):
            return None
        variant = fw.get("step4_variant") or "air"
        if variant == "holdout":
            return FirstWeekStepPlan(
                4, "dm", "first_week", "", skip_reason="step4_holdout"
            )
        return FirstWeekStepPlan(4, "dm", "first_week", _hint_step4())

    if next_step == 5:
        if msgs >= 3 or done:
            return FirstWeekStepPlan(
                5, "dm", "first_week", "", skip_reason="msgs_group_ge_3"
            )
        if days < 5 and hours < 96:
            if days < 4 or hours < 100:
                return None
        if not in_send_window(now, tz_name=tz_name):
            return None
        return FirstWeekStepPlan(5, "dm", "first_week", _hint_step5())

    if next_step == 6:
        if days < 7 and hours < 150:
            if days < 6 or hours < 144:
                return None
        if not in_send_window(now, tz_name=tz_name):
            return None
        return FirstWeekStepPlan(6, "dm", "first_week", _hint_step6())

    return None


def catchup_completed_step(fw_started_at: datetime, *, now: Optional[datetime] = None) -> int:
    """
    Для backfill: какой step считать уже пройденным без отправки,
    чтобы не сыпать просроченные касания пачкой.
    Возвращает last completed step (1..5); 6 не автозавершаем — его ещё можно послать.
    """
    now = now or datetime.now(MSK)
    started = fw_started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=MSK)
    started = started.astimezone(MSK)
    now = now.astimezone(MSK)
    hours = (now - started).total_seconds() / 3600.0
    days = (now.date() - started.date()).days

    completed = 1  # welcome в группе — уже был или пропускаем
    if hours >= 2:
        completed = 2
    if days >= 1 and hours >= 20:
        completed = 3
    if days >= 2 and hours >= 44:
        completed = 4
    if days >= 4 and hours >= 100:
        completed = 5
    return completed
