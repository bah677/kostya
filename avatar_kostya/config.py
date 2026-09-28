"""
Конфигурация автономного проекта «БиблияБот».

- BibliaBotConfig — токен бота, имя БД и проверки перед стартом (main.py).
- config (AppConfig) — то, что ждут модули bot/* / openai_client: админка, YooKassa,
  нули для клуб-полей, если в боте Библии они не используются.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Optional

from dotenv import load_dotenv

_CONFIG_DIR = Path(__file__).resolve().parent
# Явно подгружаем `.env` из каталога проекта (рядом с этим файлом),
# чтобы переменные находились независимо от текущей рабочей директории.
load_dotenv(_CONFIG_DIR / ".env")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Процесс бота «Библия» (точка входа main.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BibliaBotConfig:
    """Токен бота, имя базы, общий Postgres и OpenAI."""

    BIBLIA_BOT_TOKEN: str
    BIBLIA_DB_NAME: str
    OPENAI_API_KEY: str
    DB_HOST: str
    DB_PORT: str
    DB_USER: str
    DB_PASSWORD: str
    LOG_LEVEL: str = "INFO"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.BIBLIA_DB_NAME}"
        )


def validate_biblia_bot_startup(bc: BibliaBotConfig) -> None:
    req = {
        "BIBLIA_BOT_TOKEN": bc.BIBLIA_BOT_TOKEN,
        "BIBLIA_DB_NAME": bc.BIBLIA_DB_NAME,
        "OPENAI_API_KEY": bc.OPENAI_API_KEY,
        "DB_HOST": bc.DB_HOST,
        "DB_PORT": bc.DB_PORT,
        "DB_USER": bc.DB_USER,
        "DB_PASSWORD": bc.DB_PASSWORD,
    }
    miss = [k for k, v in req.items() if not str(v).strip()]
    if miss:
        raise ValueError(f"Biblia: не заданы переменные: {', '.join(miss)}")
    if not (os.getenv("DEEPSEEK_API_KEY") or "").strip():
        raise ValueError("Biblia: нужен DEEPSEEK_API_KEY для агента")


def _biblia_db_name() -> str:
    """Имя БД: BIBLIA_DB_NAME или fallback на DB_NAME (как в общем .env)."""
    a = (os.getenv("BIBLIA_DB_NAME") or "").strip()
    if a:
        return a
    return (os.getenv("DB_NAME") or "").strip()


def load_biblia_bot_config() -> BibliaBotConfig:
    return BibliaBotConfig(
        BIBLIA_BOT_TOKEN=os.getenv("BIBLIA_BOT_TOKEN", "").strip(),
        BIBLIA_DB_NAME=_biblia_db_name(),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "").strip(),
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", ""),
        DB_USER=os.getenv("DB_USER", ""),
        DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
    )


# ---------------------------------------------------------------------------
# Общий runtime-конфиг для слоя bot/* (без обязательного MIRON_BOT_TOKEN)
# ---------------------------------------------------------------------------


def _parse_gift_link_validity_days(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 30
    try:
        n = int(str(raw).strip(), 10)
    except ValueError:
        return 30
    return max(1, min(3650, n))


def _normalize_env_username(raw: str) -> Optional[str]:
    if not raw or not raw.strip():
        return None
    return raw.strip().lstrip("@") or None


@dataclass(frozen=True)
class AppConfig:
    MIRON_BOT_TOKEN: str = ""
    OPENAI_API_KEY: str = ""

    ADMIN_BOT_TOKEN: Optional[str] = None  # legacy: только если нет BIBLIA_BOT_TOKEN
    BIBLIA_BOT_TOKEN: Optional[str] = None  # основной бот → ADMIN_CHANNEL API

    ADMIN_BOT_ID: int = 0
    WORKFLOW_ID: str = ""

    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""

    BZB_API_KEY: str = ""
    BZB_API_URL: str = ""

    DB_HOST: str = "localhost"
    DB_PORT: str = ""
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""

    ADMIN_CHANNEL_ID: Optional[str] = None
    PAYMENT_THREAD_ID: int = 0
    SUPPORT_THREAD_ID: int = 0

    CLUB_GROUP_ID: int = 0
    CLUB_POST_LINK: str = ""
    CLUB_INVITE_TTL_HOURS: int = 24
    CLUB_GROUP_AUDIT_HOUR_UTC: int = 21
    WELCOME_TOPIC_ID: int = 0
    REACT_TOPIC_ID: int = 0
    GIFT_LINK_VALIDITY_DAYS: int = 30
    PUBLIC_OFFER_PDF_FILE_ID: Optional[str] = None
    TELEGRAM_BOT_USERNAME: Optional[str] = None

    LOG_LEVEL: str = "INFO"
    MAX_WORKERS: int = 5

    MEDIA_INBOUND_ARCHIVE_DIR: str = "data/media_inbound"

    # Фоновый опрос статусов YooKassa/BZB (PaymentChecker). По умолчанию выключен.
    PAYMENT_CHECKER_ENABLED: bool = False
    # Фоновый опрос answered-тикетов → пользователь. В аватаре выключен.
    SUPPORT_TICKET_MONITOR_ENABLED: bool = False

    # Telegram user_id суперадмина: полный доступ без лицензии; единственный, кто /admin_add и /admin_block.
    SUPER_ADMIN_ID: int = 0
    # Личка: доступ только у супера и bot_admins; остальным — только /start и сообщение об ожидании.
    BOT_ACCESS_ADMIN_ONLY: bool = False

    # RAG (изолированный пакет ``rag/``; см. ``bot/integrations/rag_bridge.py``)
    RAG_ENABLED: bool = False
    CHROMA_PERSIST_DIR: str = "chroma_data"
    RAG_EXPERT_COLLECTION: str = "expert_materials"
    RAG_GOLDEN_COLLECTION: str = "golden_examples"
    RAG_EMBEDDING_MODEL: str = "text-embedding-3-small"
    RAG_CHUNK_SIZE_TOKENS: int = 600
    RAG_CHUNK_OVERLAP_TOKENS: int = 100
    RAG_TIKTOKEN_ENCODING: str = "cl100k_base"

    # Группа с топиками для автоматического наполнения RAG (0 = выключено).
    # Legacy: одна группа. Если задан RAG_GROUPS — этот параметр игнорируется.
    RAG_GROUP_CHAT_ID: int = 0
    RAG_MIN_INDEX_CHARS: int = 300
    RAG_TAG_MODEL: str = "gpt-4o-mini"
    # Подробные логи индекса RAG из группы (см. main._setup_logging + RAG_INDEXER_DEBUG в .env).
    RAG_INDEXER_DEBUG: bool = False
    # Реплаи в RAG-группу после индексации (✓ и ошибки); False = тихий режим, в группу не писать.
    RAG_GROUP_INDEX_REPLIES: bool = True
    # Legacy: список message_thread_id топиков группы RAG_GROUP_CHAT_ID через запятую; пусто = все.
    RAG_GROUP_TOPIC_IDS: str = ""
    # Несколько групп: «chat_id:topic1,topic2;chat_id2;chat_id3:topic5».
    # Группы через «;», топики через «,» после «:». Без топиков = все.
    # Приоритет над RAG_GROUP_CHAT_ID + RAG_GROUP_TOPIC_IDS.
    RAG_GROUPS: str = ""
    # Топики (message_thread_id), которые никогда не индексировать, во всех RAG-группах.
    RAG_EXCLUDE_TOPIC_IDS: str = ""
    # Группы/топики с отзывами клиентов (формат как RAG_GROUPS). Индексируются в ту же Chroma.
    RAG_TESTIMONIAL_GROUPS: str = ""
    # Сколько последних реплик пользователя отдавать планировщику RAG и Chroma.
    RAG_RETRIEVAL_CONTEXT_USER_TURNS: int = 4
    # Макс. чанков отзывов в промпте (если планировщик включил testimonials).
    RAG_TESTIMONIAL_MAX_CHUNKS: int = 2

    # /new: сколько последних реплик задачи подставлять в DeepSeek (creative_task_turns)
    CREATIVE_TASK_HISTORY_MAX_MESSAGES: int = 24

    # Отдельный сервис MTProto (t.me/...): скачивание файлов > лимита Bot API (~20 МБ).
    TG_MTPRO_DOWNLOADER_URL: str = ""
    TG_MTPRO_DOWNLOADER_API_KEY: str = ""

    # Яндекс.Диск → RAG (WebDAV, фоновый импорт аудио).
    YANDEX_DISK_ENABLED: bool = False
    YANDEX_DISK_LOGIN: str = ""
    YANDEX_DISK_PASSWORD: str = ""
    # JSON: {"sources":[{id, path, masks, hint, ...}]} или путь к файлу ниже.
    YANDEX_DISK_SOURCES: str = ""
    YANDEX_DISK_SOURCES_FILE: str = "config/yandex_disk_sources.json"
    YANDEX_DISK_POLL_INTERVAL_SEC: int = 3600
    YANDEX_DISK_TRANSCRIPT_HEAD_CHARS: int = 2000

    # Телемост → почта Яндекса → подтверждение в группе → RAG.
    TELEMOST_MAIL_ENABLED: bool = False
    TELEMOST_MAIL_IMAP_HOST: str = "imap.yandex.ru"
    TELEMOST_MAIL_IMAP_PORT: int = 993
    TELEMOST_MAIL_LOGIN: str = ""
    TELEMOST_MAIL_PASSWORD: str = ""
    TELEMOST_MAIL_FOLDER: str = "INBOX"
    TELEMOST_MAIL_FROM_MARKERS: str = "keeper@telemost.yandex.ru"
    TELEMOST_MAIL_POLL_INTERVAL_SEC: int = 300
    TELEMOST_MAIL_CLUB_HINT: str = ""
    TELEMOST_MAIL_AVATAR_SPEAKER_NAMES: str = ""
    TELEMOST_MAIL_DEFAULT_PRODUCT: str = ""

    # Шортсы из записей Телемоста (после «Загрузить в RAG»).
    TELEMOST_SHORTS_ENABLED: bool = False
    TELEMOST_VIDEO_SHORTS_ENABLED: bool = False
    TELEMOST_SHORTS_COUNT: int = 5
    TELEMOST_SHORTS_MAX_DURATION_SEC: int = 120
    TELEMOST_SHORTS_USE_ARENA: bool = False
    TELEMOST_SHORTS_VIDEO_DIR: str = "data/telemost_video"
    TELEMOST_SHORTS_WORK_DIR: str = "data/telemost_shorts"
    TELEMOST_SHORTS_PHILOSOPHY_HINT: str = ""
    TELEMOST_SHORTS_WAIT_RECORDING_SEC: int = 21_600
    TELEMOST_SHORTS_POLL_INTERVAL_SEC: int = 120
    TELEMOST_RECORDINGS_WEBDAV_DIR: str = "/Записи Телемоста"
    TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC: float = -0.5
    TELEMOST_SHORTS_CUT_OFFSET_SEC: float = -0.5

    TELEMOST_AUDIO_CLIPS_ENABLED: bool = False
    TELEMOST_AUDIO_CLIPS_COUNT: int = 5
    TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC: int = 90
    TELEMOST_AUDIO_CLIPS_OFFSET_SEC: float = -0.5
    TELEMOST_AUDIO_MIN_FULL_DURATION_SEC: int = 180
    TELEMOST_AUDIO_DIR: str = "data/telemost_audio"
    TELEMOST_AUDIO_WORK_DIR: str = "data/telemost_audio_clips"
    TELEMOST_AUDIO_CLUB_BOT_USERNAME: str = "Talk_God_Bot"
    TELEMOST_AUDIO_CLUB_BUTTON_TEXT: str = "Любящие Бога"

    TELEMOST_FULL_VOICE_ENABLED: bool = True
    TELEMOST_FULL_VOICE_CHAT_ID: int = 0
    TELEMOST_FULL_VOICE_TOPIC_ID: int = 1448
    TELEMOST_REELS_BRIEF_ENABLED: bool = True
    TELEMOST_REELS_BRIEF_CHAT_ID: int = 0
    TELEMOST_REELS_BRIEF_TOPIC_ID: int = 1492
    # Выбор моментов и подписи к ним — творческие задачи. На gpt-4o-mini
    # (модель для разметки) выходил плоский пересказ и куски про одно и то же.
    TELEMOST_AUDIO_MOMENTS_MODEL: str = "gpt-4.1"
    TELEMOST_AUDIO_CAPTION_MODEL: str = "gpt-4.1"
    TELEMOST_REELS_BRIEF_MODEL: str = "gpt-4.1"
    TELEMOST_REELS_BRIEF_MAX_TOKENS: int = 16000
    TELEMOST_REELS_BRIEF_CROSSCHECK_ENABLED: bool = True
    TELEMOST_REELS_BRIEF_REVIEW_MODEL: str = "deepseek-chat"
    TELEMOST_REELS_BRIEF_PROJECT_DESC: str = ""
    TELEMOST_REELS_TOP_IDEAS: int = 5
    TELEMOST_REELS_HOOK_VARIANTS: int = 6
    TELEMOST_REELS_BODY_VARIANTS: int = 2
    TELEMOST_REELS_WINDOW_PAD_SEC: float = 75.0
    TELEMOST_REELS_REVIEW_ROUNDS: int = 2
    TELEMOST_EFIR_TOPIC_ID: int = 3
    TELEMOST_MOLITVA_TOPIC_ID: int = 2
    # Топик «Покаяние» — задать позже (0 = пока не публиковать в клубной ветке).
    TELEMOST_POKAYANIE_TOPIC_ID: int = 0
    # Топик «Вопрос-ответ» — задать позже (0 = пока не публиковать в клубной ветке).
    TELEMOST_QA_TOPIC_ID: int = 0

    RAG_SHORTS_CHAT_ID: int = 0
    RAG_SHORTS_TOPIC_ID: int = 582

    # Shorts mail wizard → mailing club / biblia
    CLUB_BOT_TOKEN: str = ""
    CLUB_DB_HOST: str = "localhost"
    CLUB_DB_PORT: str = "5432"
    CLUB_DB_NAME: str = ""
    CLUB_DB_USER: str = ""
    CLUB_DB_PASSWORD: str = ""
    BIBLIA_MAIL_BOT_TOKEN: str = ""
    BIBLIA_MAIL_DB_HOST: str = "localhost"
    BIBLIA_MAIL_DB_PORT: str = "5432"
    BIBLIA_MAIL_DB_NAME: str = ""
    BIBLIA_MAIL_DB_USER: str = ""
    BIBLIA_MAIL_DB_PASSWORD: str = ""
    BIBLIA_BOT_USERNAME: str = "otvet_iz_biblii_bot"

    # Админская группа RAG: уведомления, догрузка, публичность источников (формат: -1003756916561 / 3756916561).
    RAG_ADMIN_CHAT_ID: int = 0
    RAG_ADMIN_TOPIC_ID: int = 0

    # Устаревшие алиасы (если RAG_ADMIN_* пусты).
    TELEMOST_MAIL_NOTIFY_CHAT_ID: int = 0
    TELEMOST_MAIL_NOTIFY_TOPIC_ID: int = 0
    RAG_SOURCE_VISIBILITY_CHAT_ID: int = 0
    RAG_SOURCE_VISIBILITY_TOPIC_ID: int = 0

    # Voicebox (GPU TTS / voice clone) — API с бот-сервера, UI не нужен.
    VOICEBOX_ENABLED: bool = False
    VOICEBOX_BASE_URL: str = ""
    VOICEBOX_DEFAULT_PROFILE_ID: str = ""
    VOICEBOX_ENGINE: str = "qwen"
    VOICEBOX_MODEL_SIZE: str = "1.7B"
    VOICEBOX_LANGUAGE: str = "ru"

    # Ежедневные YouTube-молитвы (тренды → TTS как в Библии → сток → TG-топик).
    YT_PRAYER_ENABLED: bool = True
    YT_PRAYER_CHAT_ID: int = -1003756916561
    YT_PRAYER_TOPIC_ID: int = 1021
    # Ночные прогоны по расписанию. 0 — фича остаётся включённой и команды
    # работают, но само расписание не поднимается.
    YT_SCHEDULE_ENABLED: bool = True
    YT_PRAYER_HOUR_MSK: int = 3
    # Час старта Shorts. None — идут одним прогоном следом за
    # горизонтальными; заданный час разводит их в отдельное расписание.
    YT_SHORTS_HOUR_MSK: Optional[int] = None
    YT_PRAYER_COUNT: int = 3
    YT_PRAYER_WORK_DIR: str = "data/youtube_prayer"
    # Не повторять темы за последние N дней (used_trends.json + done.json).
    YT_PRAYER_TREND_HISTORY_DAYS: int = 14
    YT_PRAYER_BROLL_CLIPS: int = 12
    YT_PRAYER_SCENE_SEC: int = 10
    YT_PRAYER_SCENE_POOL: int = 12
    YT_PRAYER_IMAGE_GEN: bool = True
    YT_PRAYER_IMAGE_COUNT: int = 0  # generic stills; якоря — отдельно
    YT_PRAYER_AI_ANCHORS: bool = True
    YT_PRAYER_AI_ANCHOR_COUNT: int = 8  # 6–10 смысловых пиков
    YT_PRAYER_COVER_VARIANTS: int = 4
    YT_PRAYER_HOOK_SEC: float = 2.0
    YT_PRAYER_SUBTITLE_OFFSET_SEC: float = -0.35
    # EN-пакет: 1×16:9 + 3 шортса, тренды USA, отдельный голос ElevenLabs.
    YT_PRAYER_EN_ENABLED: bool = False
    YT_PRAYER_EN_COUNT: int = 1
    YT_PRAYER_EN_VOICE_ID: str = "a4CnuaYbALRvW39mDitg"
    # Автозагрузка на YouTube с премьерой в слоты МСК (по умолчанию 9 / 15 / 21).
    YT_PRAYER_YOUTUBE_UPLOAD_ENABLED: bool = False
    YT_PRAYER_YOUTUBE_LANGS: str = "ru"
    YT_PRAYER_YOUTUBE_PREMIERE_HOURS_MSK: str = "9,15,21"
    YT_PRAYER_YOUTUBE_CLIENT_SECRETS: str = "data/youtube_prayer/youtube_client_secret.json"
    YT_PRAYER_YOUTUBE_TOKEN: str = "data/youtube_prayer/youtube_oauth_token.json"
    YT_PRAYER_YOUTUBE_CATEGORY_ID: str = "22"
    YT_PRAYER_YOUTUBE_NOTIFY_SUBSCRIBERS: bool = True
    # YouTube Shorts: 8 отдельных вертикальных молитв 1–2 мин, премьера каждые 3 ч МСК.
    YT_SHORTS_ENABLED: bool = True
    YT_SHORTS_COUNT: int = 8
    YT_SHORTS_WORK_DIR: str = "data/youtube_shorts"
    YT_SHORTS_TOPIC_ID: int = 0
    YT_SHORTS_YOUTUBE_UPLOAD_ENABLED: Optional[bool] = None
    YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK: str = "0,3,6,9,12,15,18,21"
    # Дубль Shorts голосовыми в TG-канал (нативный voice) с той же отложкой, что премьеры.
    YT_SHORTS_TG_CHANNEL_ENABLED: bool = True
    YT_SHORTS_TG_CHANNEL_ID: int = -1003995394243  # @lubyashieboga
    # Опционально: сток Pexels; без ключа — lavfi-фон.
    PEXELS_API_KEY: str = ""

    @property
    def club_mail_database_url(self) -> str:
        if not (self.CLUB_DB_NAME and self.CLUB_DB_USER and self.CLUB_DB_PASSWORD):
            return ""
        return (
            f"postgresql://{self.CLUB_DB_USER}:{self.CLUB_DB_PASSWORD}"
            f"@{self.CLUB_DB_HOST}:{self.CLUB_DB_PORT}/{self.CLUB_DB_NAME}"
        )

    @property
    def biblia_mail_database_url(self) -> str:
        if not (
            self.BIBLIA_MAIL_DB_NAME
            and self.BIBLIA_MAIL_DB_USER
            and self.BIBLIA_MAIL_DB_PASSWORD
        ):
            return ""
        return (
            f"postgresql://{self.BIBLIA_MAIL_DB_USER}:{self.BIBLIA_MAIL_DB_PASSWORD}"
            f"@{self.BIBLIA_MAIL_DB_HOST}:{self.BIBLIA_MAIL_DB_PORT}/{self.BIBLIA_MAIL_DB_NAME}"
        )

    @property
    def rag_shorts_chat_id(self) -> int:
        raw = int(self.RAG_SHORTS_CHAT_ID or 0)
        if raw:
            return _normalize_supergroup_chat_id(raw)
        return self.rag_admin_chat_id

    @property
    def rag_admin_chat_id(self) -> int:
        raw = int(self.RAG_ADMIN_CHAT_ID or 0)
        if raw:
            return _normalize_supergroup_chat_id(raw)
        for alt in (
            self.RAG_SOURCE_VISIBILITY_CHAT_ID,
            self.TELEMOST_MAIL_NOTIFY_CHAT_ID,
        ):
            a = int(alt or 0)
            if a:
                return _normalize_supergroup_chat_id(a)
        return 0

    @property
    def rag_admin_topic_id(self) -> int:
        for val in (
            self.RAG_ADMIN_TOPIC_ID,
            self.RAG_SOURCE_VISIBILITY_TOPIC_ID,
            self.TELEMOST_MAIL_NOTIFY_TOPIC_ID,
        ):
            tid = int(val or 0)
            if tid:
                return tid
        return 0

    @property
    def rag_indexer_verbose(self) -> bool:
        """Широкий лог RAG-индексера: флаг или общий LOG_LEVEL=DEBUG."""
        if self.RAG_INDEXER_DEBUG:
            return True
        return str(self.LOG_LEVEL or "").strip().upper() == "DEBUG"

    @property
    def rag_exclude_topic_ids(self) -> FrozenSet[int]:
        """Глобальный denylist топиков (админские ветки и т.п.)."""
        return _parse_rag_group_topic_allowlist(self.RAG_EXCLUDE_TOPIC_IDS) or frozenset()

    @property
    def rag_group_topic_allowlist(self) -> Optional[frozenset[int]]:
        """None = все топики; иначе только перечисленные message_thread_id.

        Legacy-свойство для одной группы. Для нескольких — ``rag_groups_map``.
        """
        return _parse_rag_group_topic_allowlist(self.RAG_GROUP_TOPIC_IDS)

    @property
    def rag_groups_map(self) -> Dict[int, Optional[frozenset[int]]]:
        """Все RAG-группы: {chat_id: frozenset(topic_ids) | None}.

        None в значении = все топики группы.
        Если RAG_GROUPS задан — берём оттуда; иначе fallback на RAG_GROUP_CHAT_ID.
        """
        return _parse_rag_groups(
            self.RAG_GROUPS,
            fallback_chat_id=self.RAG_GROUP_CHAT_ID,
            fallback_topic_ids=self.RAG_GROUP_TOPIC_IDS,
        )

    @property
    def rag_testimonial_groups_map(self) -> Dict[int, Optional[frozenset[int]]]:
        """Группы с отзывами клиентов: {chat_id: frozenset(topic_ids) | None}."""
        return _parse_rag_groups(self.RAG_TESTIMONIAL_GROUPS)

    @property
    def rag_index_groups_map(self) -> Dict[int, Optional[frozenset[int]]]:
        """Объединение expert + testimonial групп для регистрации хендлера индексации."""
        merged: Dict[int, Optional[frozenset[int]]] = {}
        for src in (self.rag_groups_map, self.rag_testimonial_groups_map):
            for gid, topics in src.items():
                if gid not in merged:
                    merged[gid] = topics
                    continue
                prev = merged[gid]
                if prev is None or topics is None:
                    merged[gid] = None
                else:
                    merged[gid] = prev | topics
        return merged

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def telegram_token_for_admin_channel(self) -> Optional[str]:
        """
        Токен Bot API для createForumTopic/sendMessage в ADMIN_CHANNEL: тот же,
        что основной бот (BIBLIA_BOT_TOKEN). Иначе — legacy ADMIN_BOT_TOKEN.
        """
        main = (self.BIBLIA_BOT_TOKEN or "").strip()
        if main:
            return main
        return (self.ADMIN_BOT_TOKEN or "").strip() or None

    @property
    def has_admin_bot(self) -> bool:
        return bool(self.telegram_token_for_admin_channel)

    @property
    def has_admin_channel(self) -> bool:
        return bool(self.ADMIN_CHANNEL_ID)

    @property
    def has_yookassa(self) -> bool:
        return bool(self.YOOKASSA_SHOP_ID and self.YOOKASSA_SECRET_KEY)

    @property
    def media_inbound_archive_enabled(self) -> bool:
        v = str(self.MEDIA_INBOUND_ARCHIVE_DIR or "").strip().lower()
        if not v or v in ("0", "false", "no", "off"):
            return False
        return True

    @property
    def resolved_media_inbound_archive_root(self) -> Path:
        raw = Path(self.MEDIA_INBOUND_ARCHIVE_DIR.strip())
        if raw.is_absolute():
            return raw
        return Path(__file__).resolve().parent / raw

    @property
    def resolved_chroma_persist_dir(self) -> Path:
        """Каталог Chroma относительно каталога проекта (рядом с config.py), если путь не абсолютный."""
        raw = Path(str(self.CHROMA_PERSIST_DIR or "").strip() or "chroma_data")
        if raw.is_absolute():
            return raw
        return Path(__file__).resolve().parent / raw


def _app_db_name() -> str:
    """DB_NAME в .env или fallback на BIBLIA_DB_NAME — та же БД, что и у BibliaBotConfig."""
    a = (os.getenv("DB_NAME") or "").strip()
    if a:
        return a
    return (os.getenv("BIBLIA_DB_NAME") or "").strip()


def _safe_int_env(key: str, default: int, *, min_v: int = 0, max_v: int = 10_000) -> int:
    try:
        n = int((os.getenv(key) or str(default)).strip(), 10)
    except ValueError:
        return default
    return max(min_v, min(max_v, n))


def _normalize_supergroup_chat_id(raw: int) -> int:
    n = int(raw or 0)
    if n == 0:
        return 0
    if n > 0:
        s = str(n)
        if not s.startswith("100") and len(s) >= 9:
            return int(f"-100{n}")
    return n


def _parse_super_admin_id(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 0
    try:
        return int(str(raw).strip(), 10)
    except ValueError:
        return 0


def _parse_rag_group_topic_allowlist(raw: Optional[str]) -> Optional[FrozenSet[int]]:
    s = (raw or "").strip()
    if not s:
        return None
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part, 10))
        except ValueError:
            logging.getLogger(__name__).warning(
                "RAG_GROUP_TOPIC_IDS: пропуск нечислового фрагмента %r", part
            )
    if not out:
        return None
    return frozenset(out)


def _parse_rag_groups(
    raw_groups: Optional[str],
    *,
    fallback_chat_id: int = 0,
    fallback_topic_ids: str = "",
) -> Dict[int, Optional[FrozenSet[int]]]:
    """Парсинг ``RAG_GROUPS`` → ``{chat_id: frozenset | None}``.

    Формат: ``chat_id:topic1,topic2;chat_id2;chat_id3:topic5``
    - Группы через ``;``
    - Топики через ``,`` после ``:``; без топиков = все
    - Если ``raw_groups`` пуст — fallback на ``RAG_GROUP_CHAT_ID`` + ``RAG_GROUP_TOPIC_IDS``
    """
    _log = logging.getLogger(__name__)
    result: Dict[int, Optional[FrozenSet[int]]] = {}

    s = (raw_groups or "").strip()
    if s:
        for entry in s.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                gid_raw, topics_raw = entry.split(":", 1)
            else:
                gid_raw, topics_raw = entry, ""
            try:
                gid = int(gid_raw.strip(), 10)
            except ValueError:
                _log.warning("RAG_GROUPS: пропуск нечислового chat_id %r", gid_raw)
                continue
            if not gid:
                continue
            allow = _parse_rag_group_topic_allowlist(topics_raw.strip())
            result[gid] = allow
        return result

    gid = int(fallback_chat_id or 0)
    if gid:
        result[gid] = _parse_rag_group_topic_allowlist(fallback_topic_ids)
    return result


def _env_flag_true(key: str, default: bool = False) -> bool:
    v = (os.getenv(key) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def load_app_config() -> AppConfig:
    media_raw = os.getenv("MEDIA_INBOUND_ARCHIVE_DIR", "data/media_inbound")
    return AppConfig(
        MIRON_BOT_TOKEN=os.getenv("MIRON_BOT_TOKEN", ""),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        WORKFLOW_ID=os.getenv("WORKFLOW_ID", ""),
        YOOKASSA_SHOP_ID=os.getenv("YOOKASSA_SHOP_ID", ""),
        YOOKASSA_SECRET_KEY=os.getenv("YOOKASSA_SECRET_KEY", ""),
        BZB_API_KEY=os.getenv("BZB_API_KEY", ""),
        BZB_API_URL=os.getenv("BZB_API_URL", ""),
        ADMIN_BOT_TOKEN=os.getenv("ADMIN_BOT_TOKEN"),
        BIBLIA_BOT_TOKEN=(os.getenv("BIBLIA_BOT_TOKEN") or "").strip() or None,
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", ""),
        DB_NAME=_app_db_name(),
        DB_USER=os.getenv("DB_USER", ""),
        DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
        ADMIN_CHANNEL_ID=os.getenv("ADMIN_CHANNEL_ID"),
        PAYMENT_THREAD_ID=int(os.getenv("PAYMENT_THREAD_ID", "0")),
        SUPPORT_THREAD_ID=int(os.getenv("SUPPORT_THREAD_ID", "0")),
        ADMIN_BOT_ID=int(os.getenv("ADMIN_BOT_ID", "0")),
        CLUB_GROUP_ID=int(os.getenv("CLUB_GROUP_ID", "0")),
        CLUB_POST_LINK=(os.getenv("CLUB_POST_LINK") or "").strip(),
        CLUB_INVITE_TTL_HOURS=int(os.getenv("CLUB_INVITE_TTL_HOURS", "24")),
        CLUB_GROUP_AUDIT_HOUR_UTC=int(os.getenv("CLUB_GROUP_AUDIT_HOUR_UTC", "21")) % 24,
        WELCOME_TOPIC_ID=int(os.getenv("WELCOME_TOPIC_ID", "0")),
        REACT_TOPIC_ID=int(os.getenv("REACT_TOPIC_ID", "0")),
        GIFT_LINK_VALIDITY_DAYS=_parse_gift_link_validity_days(os.getenv("GIFT_LINK_VALIDITY_DAYS")),
        PUBLIC_OFFER_PDF_FILE_ID=((os.getenv("PUBLIC_OFFER_PDF_FILE_ID") or "").strip() or None),
        TELEGRAM_BOT_USERNAME=_normalize_env_username(os.getenv("TELEGRAM_BOT_USERNAME", "")),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        MEDIA_INBOUND_ARCHIVE_DIR=media_raw,
        PAYMENT_CHECKER_ENABLED=_env_flag_true("PAYMENT_CHECKER_ENABLED", default=False),
        SUPPORT_TICKET_MONITOR_ENABLED=_env_flag_true(
            "SUPPORT_TICKET_MONITOR_ENABLED", default=False
        ),
        SUPER_ADMIN_ID=_parse_super_admin_id(os.getenv("SUPER_ADMIN_ID")),
        BOT_ACCESS_ADMIN_ONLY=_env_flag_true("BOT_ACCESS_ADMIN_ONLY", default=False),
        RAG_ENABLED=_env_flag_true("RAG_ENABLED", default=False),
        CHROMA_PERSIST_DIR=os.getenv("CHROMA_PERSIST_DIR", "chroma_data").strip()
        or "chroma_data",
        RAG_EXPERT_COLLECTION=os.getenv("RAG_EXPERT_COLLECTION", "expert_materials").strip()
        or "expert_materials",
        RAG_GOLDEN_COLLECTION=os.getenv("RAG_GOLDEN_COLLECTION", "golden_examples").strip()
        or "golden_examples",
        RAG_EMBEDDING_MODEL=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small").strip()
        or "text-embedding-3-small",
        RAG_CHUNK_SIZE_TOKENS=_safe_int_env(
            "RAG_CHUNK_SIZE_TOKENS", 600, min_v=100, max_v=4000
        ),
        RAG_CHUNK_OVERLAP_TOKENS=_safe_int_env(
            "RAG_CHUNK_OVERLAP_TOKENS", 100, min_v=0, max_v=2000
        ),
        RAG_TIKTOKEN_ENCODING=os.getenv("RAG_TIKTOKEN_ENCODING", "cl100k_base").strip()
        or "cl100k_base",
        RAG_GROUP_CHAT_ID=int(os.getenv("RAG_GROUP_CHAT_ID", "0") or 0),
        RAG_MIN_INDEX_CHARS=_safe_int_env(
            "RAG_MIN_INDEX_CHARS", 300, min_v=1, max_v=50_000
        ),
        RAG_TAG_MODEL=os.getenv("RAG_TAG_MODEL", "gpt-4o-mini").strip()
        or "gpt-4o-mini",
        RAG_INDEXER_DEBUG=_env_flag_true("RAG_INDEXER_DEBUG", default=False),
        RAG_GROUP_INDEX_REPLIES=_env_flag_true("RAG_GROUP_INDEX_REPLIES", default=True),
        RAG_GROUP_TOPIC_IDS=(os.getenv("RAG_GROUP_TOPIC_IDS") or "").strip(),
        RAG_GROUPS=(os.getenv("RAG_GROUPS") or "").strip(),
        RAG_EXCLUDE_TOPIC_IDS=(os.getenv("RAG_EXCLUDE_TOPIC_IDS") or "").strip(),
        RAG_TESTIMONIAL_GROUPS=(os.getenv("RAG_TESTIMONIAL_GROUPS") or "").strip(),
        RAG_RETRIEVAL_CONTEXT_USER_TURNS=_safe_int_env(
            "RAG_RETRIEVAL_CONTEXT_USER_TURNS", 4, min_v=1, max_v=12
        ),
        RAG_TESTIMONIAL_MAX_CHUNKS=_safe_int_env(
            "RAG_TESTIMONIAL_MAX_CHUNKS", 2, min_v=0, max_v=6
        ),
        CREATIVE_TASK_HISTORY_MAX_MESSAGES=_safe_int_env(
            "CREATIVE_TASK_HISTORY_MAX_MESSAGES", 24, min_v=4, max_v=64
        ),
        TG_MTPRO_DOWNLOADER_URL=(os.getenv("TG_MTPRO_DOWNLOADER_URL") or "").strip(),
        TG_MTPRO_DOWNLOADER_API_KEY=(os.getenv("TG_MTPRO_DOWNLOADER_API_KEY") or "").strip(),
        YANDEX_DISK_ENABLED=_env_flag_true("YANDEX_DISK_ENABLED", default=False),
        YANDEX_DISK_LOGIN=(os.getenv("YANDEX_DISK_LOGIN") or "").strip(),
        YANDEX_DISK_PASSWORD=(os.getenv("YANDEX_DISK_PASSWORD") or "").strip(),
        YANDEX_DISK_SOURCES=(os.getenv("YANDEX_DISK_SOURCES") or "").strip(),
        YANDEX_DISK_SOURCES_FILE=(
            os.getenv("YANDEX_DISK_SOURCES_FILE") or "config/yandex_disk_sources.json"
        ).strip(),
        YANDEX_DISK_POLL_INTERVAL_SEC=_safe_int_env(
            "YANDEX_DISK_POLL_INTERVAL_SEC", 3600, min_v=300, max_v=86400
        ),
        YANDEX_DISK_TRANSCRIPT_HEAD_CHARS=_safe_int_env(
            "YANDEX_DISK_TRANSCRIPT_HEAD_CHARS", 2000, min_v=200, max_v=8000
        ),
        TELEMOST_MAIL_ENABLED=_env_flag_true("TELEMOST_MAIL_ENABLED", default=False),
        TELEMOST_MAIL_IMAP_HOST=(
            os.getenv("TELEMOST_MAIL_IMAP_HOST") or "imap.yandex.ru"
        ).strip(),
        TELEMOST_MAIL_IMAP_PORT=_safe_int_env(
            "TELEMOST_MAIL_IMAP_PORT", 993, min_v=1, max_v=65535
        ),
        TELEMOST_MAIL_LOGIN=(os.getenv("TELEMOST_MAIL_LOGIN") or "").strip(),
        TELEMOST_MAIL_PASSWORD=(os.getenv("TELEMOST_MAIL_PASSWORD") or "").strip(),
        TELEMOST_MAIL_FOLDER=(os.getenv("TELEMOST_MAIL_FOLDER") or "INBOX").strip(),
        TELEMOST_MAIL_FROM_MARKERS=(
            os.getenv("TELEMOST_MAIL_FROM_MARKERS") or "keeper@telemost.yandex.ru"
        ).strip(),
        TELEMOST_MAIL_POLL_INTERVAL_SEC=_safe_int_env(
            "TELEMOST_MAIL_POLL_INTERVAL_SEC", 300, min_v=60, max_v=86400
        ),
        RAG_ADMIN_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("RAG_ADMIN_CHAT_ID", "0") or 0)
        ),
        RAG_ADMIN_TOPIC_ID=int(os.getenv("RAG_ADMIN_TOPIC_ID", "0") or 0),
        TELEMOST_MAIL_NOTIFY_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("TELEMOST_MAIL_NOTIFY_CHAT_ID", "0") or 0)
        ),
        TELEMOST_MAIL_NOTIFY_TOPIC_ID=int(
            os.getenv("TELEMOST_MAIL_NOTIFY_TOPIC_ID", "0") or 0
        ),
        TELEMOST_MAIL_CLUB_HINT=(os.getenv("TELEMOST_MAIL_CLUB_HINT") or "").strip(),
        TELEMOST_MAIL_AVATAR_SPEAKER_NAMES=(
            os.getenv("TELEMOST_MAIL_AVATAR_SPEAKER_NAMES") or ""
        ).strip(),
        TELEMOST_MAIL_DEFAULT_PRODUCT=(
            os.getenv("TELEMOST_MAIL_DEFAULT_PRODUCT") or ""
        ).strip(),
        TELEMOST_SHORTS_ENABLED=_env_flag_true("TELEMOST_SHORTS_ENABLED", default=False),
        TELEMOST_VIDEO_SHORTS_ENABLED=_env_flag_true(
            "TELEMOST_VIDEO_SHORTS_ENABLED", default=False
        ),
        TELEMOST_SHORTS_COUNT=_safe_int_env(
            "TELEMOST_SHORTS_COUNT", 5, min_v=1, max_v=10
        ),
        TELEMOST_SHORTS_MAX_DURATION_SEC=_safe_int_env(
            "TELEMOST_SHORTS_MAX_DURATION_SEC", 120, min_v=45, max_v=120
        ),
        TELEMOST_SHORTS_USE_ARENA=_env_flag_true(
            "TELEMOST_SHORTS_USE_ARENA", default=False
        ),
        TELEMOST_SHORTS_VIDEO_DIR=(
            os.getenv("TELEMOST_SHORTS_VIDEO_DIR") or "data/telemost_video"
        ).strip(),
        TELEMOST_SHORTS_WORK_DIR=(
            os.getenv("TELEMOST_SHORTS_WORK_DIR") or "data/telemost_shorts"
        ).strip(),
        TELEMOST_SHORTS_PHILOSOPHY_HINT=(
            os.getenv("TELEMOST_SHORTS_PHILOSOPHY_HINT") or ""
        ).strip(),
        TELEMOST_SHORTS_WAIT_RECORDING_SEC=_safe_int_env(
            "TELEMOST_SHORTS_WAIT_RECORDING_SEC", 21_600, min_v=300, max_v=86_400
        ),
        TELEMOST_SHORTS_POLL_INTERVAL_SEC=_safe_int_env(
            "TELEMOST_SHORTS_POLL_INTERVAL_SEC", 120, min_v=30, max_v=600
        ),
        TELEMOST_RECORDINGS_WEBDAV_DIR=(
            os.getenv("TELEMOST_RECORDINGS_WEBDAV_DIR") or "/Записи Телемоста"
        ).strip(),
        TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC=float(
            os.getenv("TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC", "-0.5") or -0.5
        ),
        TELEMOST_SHORTS_CUT_OFFSET_SEC=float(
            os.getenv("TELEMOST_SHORTS_CUT_OFFSET_SEC", "-0.5") or -0.5
        ),
        TELEMOST_AUDIO_CLIPS_ENABLED=_env_flag_true(
            "TELEMOST_AUDIO_CLIPS_ENABLED", default=False
        ),
        TELEMOST_AUDIO_CLIPS_COUNT=_safe_int_env(
            "TELEMOST_AUDIO_CLIPS_COUNT", 5, min_v=1, max_v=10
        ),
        TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC=_safe_int_env(
            "TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC", 90, min_v=45, max_v=120
        ),
        TELEMOST_AUDIO_CLIPS_OFFSET_SEC=float(
            os.getenv("TELEMOST_AUDIO_CLIPS_OFFSET_SEC", "-0.5") or -0.5
        ),
        TELEMOST_AUDIO_MIN_FULL_DURATION_SEC=_safe_int_env(
            "TELEMOST_AUDIO_MIN_FULL_DURATION_SEC", 180, min_v=60, max_v=3600
        ),
        TELEMOST_AUDIO_DIR=(
            os.getenv("TELEMOST_AUDIO_DIR") or "data/telemost_audio"
        ).strip(),
        TELEMOST_AUDIO_WORK_DIR=(
            os.getenv("TELEMOST_AUDIO_WORK_DIR") or "data/telemost_audio_clips"
        ).strip(),
        TELEMOST_AUDIO_CLUB_BOT_USERNAME=(
            os.getenv("TELEMOST_AUDIO_CLUB_BOT_USERNAME") or "Talk_God_Bot"
        ).strip().lstrip("@"),
        TELEMOST_AUDIO_CLUB_BUTTON_TEXT=(
            os.getenv("TELEMOST_AUDIO_CLUB_BUTTON_TEXT")
            or "Любящие Бога"
        ).strip(),
        TELEMOST_FULL_VOICE_ENABLED=_env_flag_true(
            "TELEMOST_FULL_VOICE_ENABLED", default=True
        ),
        TELEMOST_FULL_VOICE_CHAT_ID=_normalize_supergroup_chat_id(
            int(
                os.getenv("TELEMOST_FULL_VOICE_CHAT_ID")
                or os.getenv("RAG_SHORTS_CHAT_ID", "0")
                or os.getenv("RAG_ADMIN_CHAT_ID", "0")
                or 0
            )
        ),
        TELEMOST_FULL_VOICE_TOPIC_ID=int(
            os.getenv("TELEMOST_FULL_VOICE_TOPIC_ID", "1448") or 1448
        ),
        TELEMOST_REELS_BRIEF_ENABLED=_env_flag_true(
            "TELEMOST_REELS_BRIEF_ENABLED", default=True
        ),
        TELEMOST_REELS_BRIEF_CHAT_ID=_normalize_supergroup_chat_id(
            int(
                os.getenv("TELEMOST_REELS_BRIEF_CHAT_ID")
                or os.getenv("RAG_SHORTS_CHAT_ID", "0")
                or os.getenv("RAG_ADMIN_CHAT_ID", "0")
                or 0
            )
        ),
        TELEMOST_REELS_BRIEF_TOPIC_ID=int(
            os.getenv("TELEMOST_REELS_BRIEF_TOPIC_ID", "1492") or 1492
        ),
        TELEMOST_AUDIO_MOMENTS_MODEL=(
            os.getenv("TELEMOST_AUDIO_MOMENTS_MODEL") or "gpt-4.1"
        ).strip(),
        TELEMOST_AUDIO_CAPTION_MODEL=(
            os.getenv("TELEMOST_AUDIO_CAPTION_MODEL") or "gpt-4.1"
        ).strip(),
        TELEMOST_REELS_BRIEF_MODEL=(
            os.getenv("TELEMOST_REELS_BRIEF_MODEL") or "gpt-4.1"
        ).strip()
        or "gpt-4.1",
        TELEMOST_REELS_BRIEF_MAX_TOKENS=_safe_int_env(
            "TELEMOST_REELS_BRIEF_MAX_TOKENS", 16000, min_v=4000, max_v=32000
        ),
        TELEMOST_REELS_BRIEF_CROSSCHECK_ENABLED=_env_flag_true(
            "TELEMOST_REELS_BRIEF_CROSSCHECK_ENABLED", default=True
        ),
        TELEMOST_REELS_BRIEF_REVIEW_MODEL=(
            os.getenv("TELEMOST_REELS_BRIEF_REVIEW_MODEL") or "deepseek-chat"
        ).strip()
        or "deepseek-chat",
        TELEMOST_REELS_BRIEF_PROJECT_DESC=(
            os.getenv("TELEMOST_REELS_BRIEF_PROJECT_DESC") or ""
        ).strip(),
        TELEMOST_REELS_TOP_IDEAS=_safe_int_env(
            "TELEMOST_REELS_TOP_IDEAS", 5, min_v=1, max_v=12
        ),
        TELEMOST_REELS_HOOK_VARIANTS=_safe_int_env(
            "TELEMOST_REELS_HOOK_VARIANTS", 6, min_v=3, max_v=10
        ),
        TELEMOST_REELS_BODY_VARIANTS=_safe_int_env(
            "TELEMOST_REELS_BODY_VARIANTS", 2, min_v=1, max_v=4
        ),
        TELEMOST_REELS_WINDOW_PAD_SEC=float(
            os.getenv("TELEMOST_REELS_WINDOW_PAD_SEC", "75") or 75
        ),
        TELEMOST_REELS_REVIEW_ROUNDS=_safe_int_env(
            "TELEMOST_REELS_REVIEW_ROUNDS", 2, min_v=0, max_v=3
        ),
        TELEMOST_EFIR_TOPIC_ID=int(os.getenv("TELEMOST_EFIR_TOPIC_ID", "3") or 3),
        TELEMOST_MOLITVA_TOPIC_ID=int(
            os.getenv("TELEMOST_MOLITVA_TOPIC_ID", "2") or 2
        ),
        TELEMOST_POKAYANIE_TOPIC_ID=int(
            os.getenv("TELEMOST_POKAYANIE_TOPIC_ID", "0") or 0
        ),
        TELEMOST_QA_TOPIC_ID=int(os.getenv("TELEMOST_QA_TOPIC_ID", "0") or 0),
        RAG_SHORTS_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("RAG_SHORTS_CHAT_ID", "0") or 0)
        ),
        RAG_SHORTS_TOPIC_ID=int(os.getenv("RAG_SHORTS_TOPIC_ID", "582") or 582),
        CLUB_BOT_TOKEN=(os.getenv("CLUB_BOT_TOKEN") or "").strip(),
        CLUB_DB_HOST=os.getenv("CLUB_DB_HOST", "localhost") or "localhost",
        CLUB_DB_PORT=os.getenv("CLUB_DB_PORT", "5432") or "5432",
        CLUB_DB_NAME=(os.getenv("CLUB_DB_NAME") or "").strip(),
        CLUB_DB_USER=(os.getenv("CLUB_DB_USER") or "").strip(),
        CLUB_DB_PASSWORD=os.getenv("CLUB_DB_PASSWORD") or "",
        BIBLIA_MAIL_BOT_TOKEN=(os.getenv("BIBLIA_MAIL_BOT_TOKEN") or "").strip(),
        BIBLIA_MAIL_DB_HOST=os.getenv("BIBLIA_MAIL_DB_HOST", "localhost") or "localhost",
        BIBLIA_MAIL_DB_PORT=os.getenv("BIBLIA_MAIL_DB_PORT", "5432") or "5432",
        BIBLIA_MAIL_DB_NAME=(os.getenv("BIBLIA_MAIL_DB_NAME") or "").strip(),
        BIBLIA_MAIL_DB_USER=(os.getenv("BIBLIA_MAIL_DB_USER") or "").strip(),
        BIBLIA_MAIL_DB_PASSWORD=os.getenv("BIBLIA_MAIL_DB_PASSWORD") or "",
        BIBLIA_BOT_USERNAME=(
            os.getenv("BIBLIA_BOT_USERNAME") or "otvet_iz_biblii_bot"
        ).strip().lstrip("@"),
        RAG_SOURCE_VISIBILITY_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("RAG_SOURCE_VISIBILITY_CHAT_ID", "0") or 0)
        ),
        RAG_SOURCE_VISIBILITY_TOPIC_ID=int(
            os.getenv("RAG_SOURCE_VISIBILITY_TOPIC_ID", "0") or 0
        ),
        VOICEBOX_ENABLED=_env_flag_true("VOICEBOX_ENABLED", default=False),
        VOICEBOX_BASE_URL=(os.getenv("VOICEBOX_BASE_URL") or "").strip().rstrip("/"),
        VOICEBOX_DEFAULT_PROFILE_ID=(
            os.getenv("VOICEBOX_DEFAULT_PROFILE_ID") or ""
        ).strip(),
        VOICEBOX_ENGINE=(os.getenv("VOICEBOX_ENGINE") or "qwen").strip() or "qwen",
        VOICEBOX_MODEL_SIZE=(
            os.getenv("VOICEBOX_MODEL_SIZE") or "1.7B"
        ).strip()
        or "1.7B",
        VOICEBOX_LANGUAGE=(os.getenv("VOICEBOX_LANGUAGE") or "ru").strip() or "ru",
        YT_PRAYER_ENABLED=_env_flag_true("YT_PRAYER_ENABLED", default=True),
        YT_PRAYER_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("YT_PRAYER_CHAT_ID", "-1003756916561") or -1003756916561)
        ),
        YT_PRAYER_TOPIC_ID=int(os.getenv("YT_PRAYER_TOPIC_ID", "1021") or 1021),
        YT_SCHEDULE_ENABLED=_env_flag_true("YT_SCHEDULE_ENABLED", default=True),
        YT_PRAYER_HOUR_MSK=int(os.getenv("YT_PRAYER_HOUR_MSK", "3") or 3) % 24,
        YT_SHORTS_HOUR_MSK=(
            int(os.getenv("YT_SHORTS_HOUR_MSK", "")) % 24
            if (os.getenv("YT_SHORTS_HOUR_MSK") or "").strip().isdigit()
            else None
        ),
        # 0 — не делать горизонтальные вовсе (нужно для «только Shorts»)
        YT_PRAYER_COUNT=max(
            0, min(5, int(os.getenv("YT_PRAYER_COUNT", "3") or 3))
        ),
        YT_PRAYER_WORK_DIR=(
            os.getenv("YT_PRAYER_WORK_DIR") or "data/youtube_prayer"
        ).strip()
        or "data/youtube_prayer",
        YT_PRAYER_TREND_HISTORY_DAYS=max(
            1,
            min(
                90,
                int(os.getenv("YT_PRAYER_TREND_HISTORY_DAYS", "14") or 14),
            ),
        ),
        YT_PRAYER_BROLL_CLIPS=max(
            4, min(16, int(os.getenv("YT_PRAYER_BROLL_CLIPS", "12") or 12))
        ),
        YT_PRAYER_SCENE_SEC=max(
            5, min(20, int(os.getenv("YT_PRAYER_SCENE_SEC", "10") or 10))
        ),
        YT_PRAYER_SCENE_POOL=max(
            8, min(16, int(os.getenv("YT_PRAYER_SCENE_POOL", "12") or 12))
        ),
        YT_PRAYER_IMAGE_GEN=_env_flag_true("YT_PRAYER_IMAGE_GEN", default=True),
        YT_PRAYER_IMAGE_COUNT=max(
            0, min(6, int(os.getenv("YT_PRAYER_IMAGE_COUNT", "0") or 0))
        ),
        YT_PRAYER_AI_ANCHORS=_env_flag_true("YT_PRAYER_AI_ANCHORS", default=True),
        YT_PRAYER_AI_ANCHOR_COUNT=max(
            6, min(10, int(os.getenv("YT_PRAYER_AI_ANCHOR_COUNT", "8") or 8))
        ),
        YT_PRAYER_COVER_VARIANTS=max(
            1, min(4, int(os.getenv("YT_PRAYER_COVER_VARIANTS", "4") or 4))
        ),
        YT_PRAYER_HOOK_SEC=float(os.getenv("YT_PRAYER_HOOK_SEC", "2.0") or 2.0),
        YT_PRAYER_SUBTITLE_OFFSET_SEC=float(
            os.getenv("YT_PRAYER_SUBTITLE_OFFSET_SEC", "-0.35") or -0.35
        ),
        YT_PRAYER_EN_ENABLED=_env_flag_true("YT_PRAYER_EN_ENABLED", default=False),
        YT_PRAYER_EN_COUNT=max(
            0, min(3, int(os.getenv("YT_PRAYER_EN_COUNT", "1") or 1))
        ),
        YT_PRAYER_EN_VOICE_ID=(
            os.getenv("YT_PRAYER_EN_VOICE_ID") or "a4CnuaYbALRvW39mDitg"
        ).strip()
        or "a4CnuaYbALRvW39mDitg",
        YT_PRAYER_YOUTUBE_UPLOAD_ENABLED=_env_flag_true(
            "YT_PRAYER_YOUTUBE_UPLOAD_ENABLED", default=False
        ),
        YT_PRAYER_YOUTUBE_LANGS=(
            os.getenv("YT_PRAYER_YOUTUBE_LANGS") or "ru"
        ).strip()
        or "ru",
        YT_PRAYER_YOUTUBE_PREMIERE_HOURS_MSK=(
            os.getenv("YT_PRAYER_YOUTUBE_PREMIERE_HOURS_MSK") or "9,15,21"
        ).strip()
        or "9,15,21",
        YT_PRAYER_YOUTUBE_CLIENT_SECRETS=(
            os.getenv("YT_PRAYER_YOUTUBE_CLIENT_SECRETS")
            or "data/youtube_prayer/youtube_client_secret.json"
        ).strip(),
        YT_PRAYER_YOUTUBE_TOKEN=(
            os.getenv("YT_PRAYER_YOUTUBE_TOKEN")
            or "data/youtube_prayer/youtube_oauth_token.json"
        ).strip(),
        YT_PRAYER_YOUTUBE_CATEGORY_ID=(
            os.getenv("YT_PRAYER_YOUTUBE_CATEGORY_ID") or "22"
        ).strip()
        or "22",
        YT_PRAYER_YOUTUBE_NOTIFY_SUBSCRIBERS=_env_flag_true(
            "YT_PRAYER_YOUTUBE_NOTIFY_SUBSCRIBERS", default=True
        ),
        YT_SHORTS_ENABLED=_env_flag_true("YT_SHORTS_ENABLED", default=True),
        # 0 — не делать Shorts вовсе (нужно для «только горизонтальные»)
        YT_SHORTS_COUNT=max(
            0, min(12, int(os.getenv("YT_SHORTS_COUNT", "8") or 8))
        ),
        YT_SHORTS_WORK_DIR=(os.getenv("YT_SHORTS_WORK_DIR") or "data/youtube_shorts"),
        YT_SHORTS_TOPIC_ID=int(os.getenv("YT_SHORTS_TOPIC_ID", "0") or 0),
        YT_SHORTS_YOUTUBE_UPLOAD_ENABLED=(
            _env_flag_true("YT_SHORTS_YOUTUBE_UPLOAD_ENABLED")
            if (os.getenv("YT_SHORTS_YOUTUBE_UPLOAD_ENABLED") or "").strip()
            else None
        ),
        YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK=(
            os.getenv("YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK")
            or "0,3,6,9,12,15,18,21"
        ).strip(),
        YT_SHORTS_TG_CHANNEL_ENABLED=_env_flag_true(
            "YT_SHORTS_TG_CHANNEL_ENABLED", default=True
        ),
        YT_SHORTS_TG_CHANNEL_ID=int(
            os.getenv("YT_SHORTS_TG_CHANNEL_ID", "-1003995394243")
            or -1003995394243
        ),
        PEXELS_API_KEY=(os.getenv("PEXELS_API_KEY") or "").strip(),
    )


config = load_app_config()


def russian_days_phrase(days: int) -> str:
    n = int(days)
    if n <= 0:
        n = 1
    d100 = n % 100
    if 11 <= d100 <= 14:
        return f"{n} дней"
    d10 = n % 10
    if d10 == 1:
        return f"{n} день"
    if 2 <= d10 <= 4:
        return f"{n} дня"
    return f"{n} дней"
