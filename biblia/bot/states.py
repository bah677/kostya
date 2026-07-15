from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Состояния системы поддержки."""
    waiting_for_message = State()


class PrayerStates(StatesGroup):
    """Анкета и генерация персональной молитвы (/prayer)."""
    collecting = State()
    generating = State()


class ScriptureChallengeStates(StatesGroup):
    """Челлендж чтения Писания (/challenge)."""
    intake = State()
    duration = State()
    delivery_time = State()
    planning = State()
