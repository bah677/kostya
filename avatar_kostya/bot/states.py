from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Состояния системы поддержки."""
    waiting_for_message = State()
