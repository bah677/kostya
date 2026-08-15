from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Состояния системы поддержки."""
    waiting_for_message = State()


class PrayerStates(StatesGroup):
    """Анкета и генерация персональной молитвы (/prayer)."""
    collecting = State()
    generating = State()
    waiting_stress_feedback = State()


class ScriptureChallengeStates(StatesGroup):
    """Челлендж чтения Писания (/challenge)."""
    intake = State()
    duration = State()
    delivery_time = State()
    planning = State()


class AdminPanelStates(StatesGroup):
    """Админ-панель: ввод параметров отчётов."""
    waiting_prayer_stats_date = State()
    waiting_prayer_voice_limit = State()
    waiting_prayer_voice_per_user = State()
