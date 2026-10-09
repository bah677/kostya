"""Тексты интерфейса. Модуль выбирается по языку процесса.

Файлы называются <язык>_<экран>.py. Префикс был заведён ещё до второго бота,
так что добавление испанского ничего не ломает: ru_user_menu остаётся на
месте, рядом появляется es_user_menu.
"""

from __future__ import annotations

from types import ModuleType


def start() -> ModuleType:
    """Приветствие по /start на языке этого бота."""
    from bot.langs import bot_lang

    if bot_lang() == "es":
        from bot.texts import es_start

        return es_start
    from bot.texts import ru_start

    return ru_start


def payment() -> ModuleType:
    """Тексты интерфейса донатов на языке этого бота."""
    from bot.langs import bot_lang

    if bot_lang() == "es":
        from bot.texts import es_payment

        return es_payment
    from bot.texts import ru_payment

    return ru_payment


def donation() -> ModuleType:
    """Тексты кнопок поддержки на языке этого бота."""
    from bot.langs import bot_lang

    if bot_lang() == "es":
        from bot.texts import es_donation

        return es_donation
    from bot.texts import ru_donation

    return ru_donation


def user_menu() -> ModuleType:
    """Тексты /menu на языке этого бота."""
    from bot.langs import bot_lang

    if bot_lang() == "es":
        from bot.texts import es_user_menu

        return es_user_menu
    from bot.texts import ru_user_menu

    return ru_user_menu
