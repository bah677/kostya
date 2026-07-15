from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Состояния системы поддержки."""
    waiting_for_message = State()


class AdminGiftStates(StatesGroup):
    """Админ: /gift USER_ID → ввод числа дней."""
    waiting_days = State()


class NastyaTempOnboardingStates(StatesGroup):
    """Временный онбординг бота Насти: имя → телефон → email."""
    waiting_name = State()
    waiting_phone = State()
    waiting_email = State()


class AdminRefKeyStates(StatesGroup):
    """Админ: псевдоним для нового ref_key."""
    waiting_name = State()


class MemberGiftExtensionStates(StatesGroup):
    """Подарок продления подписки участнику клуба."""
    waiting_recipient_query = State()


class WishBoardStates(StatesGroup):
    """Доска желаний: создание просьбы."""
    waiting_description = State()
    waiting_clarification = State()
    waiting_clarification_reply = State()


class WishBoardAdminStates(StatesGroup):
    """Админ: причина отказа заявки на доске желаний."""
    waiting_reject_reason = State()


class AngelPoolStates(StatesGroup):
    """Ангельский взнос на доске добрых дел."""
    waiting_amount = State()
    confirming = State()


class LegalConsentStates(StatesGroup):
    """Ожидание согласия с юридическими документами."""
    waiting_accept = State()
