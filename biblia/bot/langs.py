"""Язык экземпляра бота.

Испанский бот — это не копия проекта, а тот же код во втором процессе со
своим .env: свой токен, своя база, BOT_LANG=es. Так правки и находки
достаются обоим ботам, а не расходятся по двум веткам навсегда.

Язык решает три вещи:
  - какие фичи поднимать (испанскому пока не нужны донаты, марафон,
    рефералы, челленджи и рассылки);
  - какие промпты отдавать модели;
  - на каком языке интерфейс.

Неизвестное значение BOT_LANG означает русский — как было до появления
второго бота.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional

DEFAULT_LANG = "ru"


@dataclass(frozen=True)
class BotLang:
    code: str
    # Человеческое имя в логах и админских сообщениях.
    label: str
    # Код для ElevenLabs и прочего, где нужен ISO.
    iso: str
    # Какие фичи поднимать. Пусто — значит все (русский, как было).
    features: FrozenSet[str] = frozenset()


_PROFILES: Dict[str, BotLang] = {
    "ru": BotLang(code="ru", label="русский", iso="ru", features=frozenset()),
    # Первая версия: человек приходит с YouTube с вопросом и получает ответ
    # из Писания. Это и есть вся воронка — за командой он не приходит.
    #
    # Личной молитвы здесь пока нет намеренно. Её фича — 1780 строк живого
    # кода, где тексты вперемешку с механикой голосовых лимитов, сравнения
    # движков и разблокировки за донат. Чтобы включить её на втором языке,
    # надо аккуратно вынести строки в таблицу, а это правка боевого файла
    # русского бота под нагрузкой. Отдельным шагом, с проверкой на русском.
    #
    # Донаты, марафон, рефералы, челленджи, рассылки и админка выключены:
    # аудитории ещё нет, а каждая фича — сотня строк на перевод и поддержку.
    "es": BotLang(
        code="es",
        label="испанский",
        iso="es",
        features=frozenset({"messaging", "user_menu", "background_jobs"}),
    ),
}


def normalize_lang(lang: Optional[str]) -> str:
    key = (lang or "").strip().lower()
    if key in _PROFILES:
        return key
    # es-MX, es_419, spa → es
    for code in _PROFILES:
        if key.startswith(code):
            return code
    return DEFAULT_LANG


def bot_lang() -> str:
    """Язык этого процесса. Берётся из BOT_LANG в .env.

    Импорт config обязателен: именно он загружает .env. Без него язык могли
    бы прочитать раньше, чем файл подхвачен, — и испанский бот молча поднялся
    бы русским. Импорт повторный и дешёвый, модуль кешируется.
    """
    try:
        import config  # noqa: F401  (ради побочного эффекта load_dotenv)
    except Exception:
        pass
    return normalize_lang(os.getenv("BOT_LANG"))


def profile(lang: Optional[str] = None) -> BotLang:
    return _PROFILES[normalize_lang(lang if lang is not None else os.getenv("BOT_LANG"))]


def feature_enabled(name: str, lang: Optional[str] = None) -> bool:
    """Поднимать ли фичу на этом языке. Пустой список в профиле — все можно."""
    allowed = profile(lang).features
    return (not allowed) or (name in allowed)


def known_langs() -> List[str]:
    return list(_PROFILES)
