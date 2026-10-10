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
from typing import Any, Dict, List, Optional, Tuple

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
    # Часовой пояс аудитории: в нём считаются часы премьер. Зритель смотрит по
    # своим часам, а не по московским.
    tz: str
    # Часы премьер в этом поясе, по умолчанию.
    premiere_hours: Tuple[int, ...]
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
    # Куда идти: «в Telegram». Ссылку в описании Shorts не кликнуть, поэтому
    # в тексте называем площадку словом, а имя даём как @имя для поиска.
    telegram_hint: str
    # Чем молитва заканчивается — по этому проверяем, что модель не оборвалась.
    amen_markers: Tuple[str, ...]
    # Как молитва закрывается: эту строку требуем от модели последней.
    prayer_closing: str
    # Шортсы: сонастройка перед молитвой и призыв в конце (идут в озвучку).
    attune: str
    cta: str
    # Свой бот на каждый язык: у испанского канала свой клон библейского бота.
    bot_username_env: str = "YT_SHORTS_BOT_USERNAME"
    # Куда звать, если в .env ничего не задано. Для русского это исторический
    # хардкод из metadata.py — переменной YT_SHORTS_BOT_USERNAME в боевом .env
    # нет, и без этого значения карточка осталась бы без адреса.
    default_destination: str = ""
    # Файл истории тем, чтобы языки не пересекались.
    history_file: str = "used_trends.json"
    themes_key: str = "themes"
    aliases: Tuple[str, ...] = field(default_factory=tuple)


_PROFILES: Dict[str, LangProfile] = {
    "ru": LangProfile(
        code="ru",
        label="RU",
        geo="RU",
        tz="Europe/Moscow",
        premiere_hours=(7, 12, 16, 19, 22),
        youtube_lang="ru",
        voice_id=_SHARED_VOICE,
        hook_fallback="Тебе это знакомо?",
        hook_template="Это про тебя — {}?",
        default_hashtags=("#молитва", "#вера", "#христианство", "#утешение", "#Бог"),
        default_tags=("молитва", "христианская молитва", "вера", "утешение"),
        outro_text="Молитва по твоей ситуации",
        telegram_hint="в Telegram",
        default_destination="Talk_God_Bot",
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
        tz="America/New_York",
        premiere_hours=(7, 12, 16, 19, 22),
        youtube_lang="en",
        voice_id=_SHARED_VOICE,
        hook_fallback="Does this feel familiar?",
        hook_template="Is this your story — {}?",
        default_hashtags=("#prayer", "#faith", "#Christianity", "#comfort", "#God"),
        default_tags=("prayer", "Christian prayer", "faith", "comfort"),
        outro_text="A prayer for what you're going through",
        telegram_hint="on Telegram",
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
        # Мехико: самая большая аудитория испаноязычного молитвенного контента.
        # Богота и Лима на час впереди, Буэнос-Айрес на три — для утренних и
        # вечерних слотов разброс терпимый. Мексика с 2022 года без перехода
        # на летнее время, ZoneInfo это учитывает.
        tz="America/Mexico_City",
        # Под мексиканский распорядок: 6 — перед выходом из дома, 12 — полдень,
        # 18 — дорога домой, 21 — после позднего ужина, 23 — бессонница, одна
        # из главных тем канала.
        premiere_hours=(6, 12, 18, 21, 23),
        youtube_lang="es",
        voice_id=_SHARED_VOICE,
        hook_fallback="¿Te suena familiar?",
        hook_template="¿Es tu historia — {}?",
        default_hashtags=("#oración", "#fe", "#cristianismo", "#consuelo", "#Dios"),
        default_tags=("oración", "oración cristiana", "fe", "consuelo"),
        outro_text="Una oración para lo que estás viviendo",
        telegram_hint="en Telegram",
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


def setting(name: str, default: Any = None) -> Any:
    """Настройка из config, а если такого поля нет — прямо из окружения.

    Объект config содержит только объявленные поля. Переменные с суффиксом
    языка (YT_SHORTS_ES_COUNT, YT_SHORTS_TG_CHANNEL_ID_ES) там не объявлены,
    и getattr молча возвращал бы значение по умолчанию: настройка прописана
    в .env, а код её не видит и ведёт себя так, будто её нет.
    """
    try:
        from config import config as cfg

        if hasattr(cfg, name):
            return getattr(cfg, name)
    except Exception:
        pass
    raw = os.getenv(name)
    return default if raw is None else raw


def lang_text(lang: Optional[str], *, env_prefix: str, fallback: str) -> str:
    """Текст языка: <PREFIX>_<LANG> → профиль.

    Общая переменная без суффикса читается ТОЛЬКО для языка по умолчанию.

    Так сделано после живого промаха. Раньше общая читалась для всех, а в
    боевом .env лежит YT_SHORTS_CTA по-русски — и испанский ролик с испанской
    молитвой заканчивался словами «напиши „Аминь“ в комментариях». Настройка
    одного канала не должна протекать в другой.

    Пустая строка в переменной — это «выключить кусок», а не «нет значения»:
    YT_SHORTS_CTA_ES= уберёт призыв только у испанских роликов.
    """
    p = profile(lang)
    raw = os.getenv(f"{env_prefix}_{p.code.upper()}")
    if raw is not None:
        return raw.strip()
    if p.code == DEFAULT_LANG:
        raw = os.getenv(env_prefix)
        if raw is not None:
            return raw.strip()
    return fallback


def attune_text(lang: Optional[str]) -> str:
    return lang_text(lang, env_prefix="YT_SHORTS_ATTUNE", fallback=profile(lang).attune)


def cta_text(lang: Optional[str]) -> str:
    return lang_text(lang, env_prefix="YT_SHORTS_CTA", fallback=profile(lang).cta)


def outro_text_for(lang: Optional[str]) -> str:
    return lang_text(
        lang, env_prefix="YT_PRAYER_OUTRO_TEXT", fallback=profile(lang).outro_text
    )


def premiere_hours_for(lang: Optional[str], *, env_prefix: str) -> List[int]:
    """Часы премьер языка — в его собственном поясе.

    Порядок: <PREFIX>_<LANG> → профиль, а общий <PREFIX> без суффикса читается
    ТОЛЬКО для языка по умолчанию.

    Так сделано нарочно. Общая переменная в боевом .env настроена под Москву
    (7,12,16,19,22). Если бы её наследовали все языки, испанский канал молча
    взял бы эти же числа и трактовал их как время Мехико — а при любой правке
    русского расписания так же молча поехал бы следом. Часы чужого канала
    должны задаваться явно.

    Старое имя с суффиксом _MSK сохранено, чтобы не трогать боевой .env.
    """
    from youtube_prayer.premiere_schedule import parse_premiere_hours

    p = profile(lang)
    names = [f"{env_prefix}_{p.code.upper()}"]
    if p.code == DEFAULT_LANG:
        names.append(env_prefix)
    for name in names:
        raw = (os.getenv(name) or "").strip()
        if raw:
            return parse_premiere_hours(raw)
    return list(p.premiere_hours)


def destination_for(lang: Optional[str]) -> tuple[str, str]:
    """Куда вести зрителя: (@имя, ссылка). Второе — уже готовый URL.

    Если для языка задан свой бот — ведём в него, со стартовой меткой, по
    которой потом видно, какой ролик привёл человека.

    Если бота ещё нет, а публичный канал есть — ведём в канал. Это честнее,
    чем отправлять мексиканца в русского бота: он туда дойдёт и упрётся в
    русский интерфейс. Метки у канала нет, атрибуция появится вместе с ботом.
    """
    p = profile(lang)
    bot = (os.getenv(p.bot_username_env) or "").strip().lstrip("@")
    if bot:
        return f"@{bot}", ""
    public = (os.getenv(f"YT_SHORTS_TG_PUBLIC_{p.code.upper()}") or "").strip().lstrip("@")
    if public:
        return f"@{public}", f"https://t.me/{public}"
    if p.code == DEFAULT_LANG:
        shared = (os.getenv("YT_SHORTS_BOT_USERNAME") or "").strip().lstrip("@")
        if shared:
            return f"@{shared}", ""
    if p.default_destination:
        return f"@{p.default_destination}", ""
    return "", ""


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
