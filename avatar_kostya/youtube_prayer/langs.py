"""Языки канала молитв: один профиль на язык вместо ветвлений по коду.

До этого язык жил в коде двадцатью пятью развилками вида
``if lang == "en": … else: <русский>``, разбросанными по девяти файлам.
Пока языков было два, это работало; третий так не добавить — пришлось бы
править каждую развилку, и любая пропущенная молча отдавала бы русский
текст в испанский ролик.

Здесь язык описан данными. Новый язык — это новая запись в ``_PROFILES``
и свой промпт в ``prompts.py``; трогать логику пайплайна не нужно.

У каждого языка свой канал на YouTube, поэтому и свой OAuth-токен:
``YT_PRAYER_YOUTUBE_TOKEN_ES`` и т.д. Не задан — берётся общий
``YT_PRAYER_YOUTUBE_TOKEN``, то есть русский канал продолжает работать
ровно как раньше.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DEFAULT_LANG = "ru"

# Голос ElevenLabs. Flash v2.5 многоязычный: один и тот же голос читает
# и по-русски, и по-испански, поэтому отдельный голос заводить не обязательно.
_SHARED_VOICE = "a4CnuaYbALRvW39mDitg"


@dataclass(frozen=True)
class LangProfile:
    code: str
    # Метка в уведомлениях админам.
    label: str
    # Регион для подбора тем и для YouTube.
    geo: str
    # Код языка в метаданных ролика (snippet.defaultLanguage).
    youtube_lang: str
    voice_id: str
    # Запасной вопрос на обложке, если модель не дала свой.
    hook_fallback: str
    # Шаблон вопроса из темы: «Это про тебя — {}?»
    hook_template: str
    # Чем добить хэштеги и теги, если модель дала мало.
    default_hashtags: Tuple[str, ...]
    default_tags: Tuple[str, ...]
    # Концевая карточка со ссылкой на бота.
    outro_text: str
    # Чем молитва заканчивается — по этому проверяем, что модель не оборвалась.
    amen_markers: Tuple[str, ...]
    # Как молитва закрывается: эту строку требуем от модели последней.
    prayer_closing: str
    # Шортсы: сонастройка перед молитвой и призыв в конце (идут в озвучку).
    attune: str
    cta: str
    # Свой бот на каждый язык: у испанского канала свой клон библейского бота.
    bot_username_env: str = "YT_SHORTS_BOT_USERNAME"
    # Файл истории тем, чтобы языки не пересекались.
    history_file: str = "used_trends.json"
    themes_key: str = "themes"
    aliases: Tuple[str, ...] = field(default_factory=tuple)


_PROFILES: Dict[str, LangProfile] = {
    "ru": LangProfile(
        code="ru",
        label="RU",
        geo="RU",
        youtube_lang="ru",
        voice_id=_SHARED_VOICE,
        hook_fallback="Тебе это знакомо?",
        hook_template="Это про тебя — {}?",
        default_hashtags=("#молитва", "#вера", "#христианство", "#утешение", "#Бог"),
        default_tags=("молитва", "христианская молитва", "вера", "утешение"),
        outro_text="Молитва по твоей ситуации",
        amen_markers=("аминь",),
        prayer_closing="Во имя Иисуса Христа, Аминь",
        attune="Закрой глаза и давай вместе помолимся.",
        cta="Если эта молитва про тебя — напиши «Аминь» в комментариях.",
        history_file="used_trends.json",
        themes_key="themes",
        aliases=("rus", "ru-ru"),
    ),
    "en": LangProfile(
        code="en",
        label="EN/US",
        geo="US",
        youtube_lang="en",
        voice_id=_SHARED_VOICE,
        hook_fallback="Does this feel familiar?",
        hook_template="Is this your story — {}?",
        default_hashtags=("#prayer", "#faith", "#Christianity", "#comfort", "#God"),
        default_tags=("prayer", "Christian prayer", "faith", "comfort"),
        outro_text="A prayer for what you're going through",
        amen_markers=("amen",),
        prayer_closing="In the name of Jesus Christ, Amen",
        attune="Close your eyes and let us pray together.",
        cta="If this prayer is for you, write «Amen» in the comments.",
        history_file="used_trends_en.json",
        themes_key="themes_en",
        aliases=("eng", "en-us"),
    ),
    # Испанский нейтральный, с прицелом на Латинскую Америку: там и самая
    # большая аудитория молитвенного контента, и Telegram у трети населения
    # (Мексика 34%, Бразилия 38%) против 9% в США. Реклама в ЛатАм платит
    # меньше всех, но основные деньги не в ней, а в переходах в бота.
    "es": LangProfile(
        code="es",
        label="ES/LatAm",
        geo="MX",
        youtube_lang="es",
        voice_id=_SHARED_VOICE,
        hook_fallback="¿Te suena familiar?",
        hook_template="¿Es tu historia — {}?",
        default_hashtags=("#oración", "#fe", "#cristianismo", "#consuelo", "#Dios"),
        default_tags=("oración", "oración cristiana", "fe", "consuelo"),
        outro_text="Una oración para lo que estás viviendo",
        amen_markers=("amén", "amen"),
        prayer_closing="En el nombre de Jesucristo, amén",
        attune="Cierra los ojos y oremos juntos.",
        cta="Si esta oración es para ti, escribe «Amén» en los comentarios.",
        bot_username_env="YT_SHORTS_BOT_USERNAME_ES",
        history_file="used_trends_es.json",
        themes_key="themes_es",
        aliases=("spa", "es-mx", "es-419"),
    ),
}

_ALIAS_TO_CODE: Dict[str, str] = {}
for _code, _prof in _PROFILES.items():
    _ALIAS_TO_CODE[_code] = _code
    for _a in _prof.aliases:
        _ALIAS_TO_CODE[_a] = _code


def normalize_lang(lang: Optional[str]) -> str:
    """Код языка. Неизвестное значение — русский, как было до таблицы."""
    key = (lang or "").strip().lower()
    return _ALIAS_TO_CODE.get(key, DEFAULT_LANG)


def profile(lang: Optional[str]) -> LangProfile:
    return _PROFILES[normalize_lang(lang)]


def known_langs() -> List[str]:
    return list(_PROFILES)


def voice_id_for(lang: Optional[str]) -> str:
    """Голос языка. Переопределяется YT_PRAYER_VOICE_ID_<LANG> в .env."""
    p = profile(lang)
    override = (os.getenv(f"YT_PRAYER_VOICE_ID_{p.code.upper()}") or "").strip()
    return override or p.voice_id


def _env_override(name: str, fallback: str) -> str:
    """Переопределение из .env: пустая строка выключает кусок, не заданная — оставляет."""
    raw = os.getenv(name)
    return fallback if raw is None else raw.strip()


def attune_text(lang: Optional[str]) -> str:
    return _env_override(f"YT_SHORTS_ATTUNE_{profile(lang).code.upper()}",
                         _env_override("YT_SHORTS_ATTUNE", profile(lang).attune))


def cta_text(lang: Optional[str]) -> str:
    return _env_override(f"YT_SHORTS_CTA_{profile(lang).code.upper()}",
                         _env_override("YT_SHORTS_CTA", profile(lang).cta))


def bot_username_for(lang: Optional[str]) -> str:
    """Имя бота, куда ведём зрителей этого языка.

    У испанского канала точка монетизации — свой клон библейского бота,
    поэтому имя берётся из отдельной переменной. Не задана — общий бот.
    """
    p = profile(lang)
    name = (os.getenv(p.bot_username_env) or "").strip()
    if not name and p.bot_username_env != "YT_SHORTS_BOT_USERNAME":
        name = (os.getenv("YT_SHORTS_BOT_USERNAME") or "").strip()
    return name.lstrip("@")
