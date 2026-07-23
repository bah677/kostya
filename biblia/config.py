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
from typing import Optional

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


def _parse_speechkit_speed(raw: Optional[str]) -> float:
    if raw is None or not str(raw).strip():
        return 0.95
    try:
        v = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return 0.95
    return max(0.5, min(2.0, v))


def _parse_voicebox_atempo(raw: Optional[str]) -> float:
    if raw is None or not str(raw).strip():
        return 0.92
    try:
        v = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return 0.92
    return max(0.5, min(1.2, v))


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


def _parse_bool_env(raw: Optional[str], default: bool = False) -> bool:
    if raw is None or not str(raw).strip():
        return default
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


@dataclass(frozen=True)
class AppConfig:
    MIRON_BOT_TOKEN: str = ""
    OPENAI_API_KEY: str = ""

    WORKFLOW_ID: str = ""

    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""

    BZB_API_KEY: str = ""
    BZB_API_URL: str = ""

    DONATION_PROVIDER_RUB: str = "bzb"
    DONATION_PROVIDER_USD: str = "bzb"
    DONATION_PROVIDER_EUR: str = "bzb"
    DONATION_RECURRING_ENABLED: bool = False

    DB_HOST: str = "localhost"
    DB_PORT: str = ""
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""

    ADMIN_CHANNEL_ID: Optional[str] = None
    PAYMENT_THREAD_ID: int = 0
    SUPER_ADMIN_ID: int = 0
    SUPPORT_THREAD_ID: int = 0
    BIBLIA_REPORT_THREAD_ID: int = 0
    MEDIA_ID_TOPIC_ID: int = 0

    CLUB_GROUP_ID: int = 0
    CLUB_POST_LINK: str = ""
    CLUB_INVITE_TTL_HOURS: int = 24
    CLUB_GROUP_AUDIT_HOUR_UTC: int = 21
    WELCOME_TOPIC_ID: int = 0
    REACT_TOPIC_ID: int = 0
    GIFT_LINK_VALIDITY_DAYS: int = 30
    PUBLIC_OFFER_PDF_FILE_ID: Optional[str] = None
    TELEGRAM_BOT_USERNAME: Optional[str] = None

    YANDEX_SPEECHKIT_API_KEY: Optional[str] = None
    YANDEX_CLOUD_FOLDER_ID: Optional[str] = None
    YANDEX_SPEECHKIT_VOICE: str = "zahar"
    YANDEX_SPEECHKIT_SPEED: float = 0.9
    YANDEX_SPEECHKIT_EMOTION: str = "neutral"

    # Voicebox (GPU clone) — приоритетный TTS для /prayer при VOICEBOX_ENABLED=1.
    VOICEBOX_ENABLED: bool = False
    VOICEBOX_BASE_URL: str = ""
    VOICEBOX_PROFILE_ID: str = ""
    VOICEBOX_ENGINE: str = "qwen"
    VOICEBOX_MODEL_SIZE: str = "1.7B"
    VOICEBOX_LANGUAGE: str = "ru"
    VOICEBOX_INSTRUCT: str = (
        "Warm natural prayerful speech, gentle rhythm, slight emotional variation, "
        "not monotone and not robotic. Soft unhurried pace. "
        "The final word амИнь: stress on capital И (a-MÍN), clear and solemn."
    )
    VOICEBOX_ATEMPO: float = 0.92

    LOG_LEVEL: str = "INFO"
    MAX_WORKERS: int = 5

    MEDIA_INBOUND_ARCHIVE_DIR: str = "data/media_inbound"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def has_admin_channel(self) -> bool:
        return bool(self.ADMIN_CHANNEL_ID)

    def resolved_admin_group_id(self) -> int:
        """Числовой id админ-супергруппы для хендлеров (reply на тикеты)."""
        raw = (self.ADMIN_CHANNEL_ID or "").strip()
        if not raw:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

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


def _app_db_name() -> str:
    """DB_NAME в .env или fallback на BIBLIA_DB_NAME — та же БД, что и у BibliaBotConfig."""
    a = (os.getenv("DB_NAME") or "").strip()
    if a:
        return a
    return (os.getenv("BIBLIA_DB_NAME") or "").strip()


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
        DONATION_PROVIDER_RUB=(os.getenv("DONATION_PROVIDER_RUB", "bzb") or "bzb").strip().lower(),
        DONATION_PROVIDER_USD=(os.getenv("DONATION_PROVIDER_USD", "bzb") or "bzb").strip().lower(),
        DONATION_PROVIDER_EUR=(os.getenv("DONATION_PROVIDER_EUR", "bzb") or "bzb").strip().lower(),
        DONATION_RECURRING_ENABLED=_parse_bool_env(
            os.getenv("DONATION_RECURRING_ENABLED"), False
        ),
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", ""),
        DB_NAME=_app_db_name(),
        DB_USER=os.getenv("DB_USER", ""),
        DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
        ADMIN_CHANNEL_ID=os.getenv("ADMIN_CHANNEL_ID"),
        PAYMENT_THREAD_ID=int(os.getenv("PAYMENT_THREAD_ID", "0")),
        SUPER_ADMIN_ID=int(os.getenv("SUPER_ADMIN_ID", "0") or "0"),
        SUPPORT_THREAD_ID=int(os.getenv("SUPPORT_THREAD_ID", "0")),
        BIBLIA_REPORT_THREAD_ID=int(os.getenv("BIBLIA_REPORT_THREAD_ID", "0") or "0"),
        MEDIA_ID_TOPIC_ID=int(os.getenv("MEDIA_ID_TOPIC_ID", "0")),
        CLUB_GROUP_ID=int(os.getenv("CLUB_GROUP_ID", "0")),
        CLUB_POST_LINK=(os.getenv("CLUB_POST_LINK") or "").strip(),
        CLUB_INVITE_TTL_HOURS=int(os.getenv("CLUB_INVITE_TTL_HOURS", "24")),
        CLUB_GROUP_AUDIT_HOUR_UTC=int(os.getenv("CLUB_GROUP_AUDIT_HOUR_UTC", "21")) % 24,
        WELCOME_TOPIC_ID=int(os.getenv("WELCOME_TOPIC_ID", "0")),
        REACT_TOPIC_ID=int(os.getenv("REACT_TOPIC_ID", "0")),
        GIFT_LINK_VALIDITY_DAYS=_parse_gift_link_validity_days(os.getenv("GIFT_LINK_VALIDITY_DAYS")),
        PUBLIC_OFFER_PDF_FILE_ID=((os.getenv("PUBLIC_OFFER_PDF_FILE_ID") or "").strip() or None),
        TELEGRAM_BOT_USERNAME=_normalize_env_username(os.getenv("TELEGRAM_BOT_USERNAME", "")),
        YANDEX_SPEECHKIT_API_KEY=(os.getenv("YANDEX_SPEECHKIT_API_KEY") or "").strip() or None,
        YANDEX_CLOUD_FOLDER_ID=(os.getenv("YANDEX_CLOUD_FOLDER_ID") or "").strip() or None,
        YANDEX_SPEECHKIT_VOICE=(os.getenv("YANDEX_SPEECHKIT_VOICE") or "zahar").strip().lower(),
        YANDEX_SPEECHKIT_SPEED=_parse_speechkit_speed(os.getenv("YANDEX_SPEECHKIT_SPEED")),
        YANDEX_SPEECHKIT_EMOTION=(os.getenv("YANDEX_SPEECHKIT_EMOTION") or "neutral").strip(),
        VOICEBOX_ENABLED=_parse_bool_env(os.getenv("VOICEBOX_ENABLED"), False),
        VOICEBOX_BASE_URL=(os.getenv("VOICEBOX_BASE_URL") or "").strip().rstrip("/"),
        VOICEBOX_PROFILE_ID=(os.getenv("VOICEBOX_PROFILE_ID") or "").strip(),
        VOICEBOX_ENGINE=(os.getenv("VOICEBOX_ENGINE") or "qwen").strip() or "qwen",
        VOICEBOX_MODEL_SIZE=(
            os.getenv("VOICEBOX_MODEL_SIZE") or "1.7B"
        ).strip()
        or "1.7B",
        VOICEBOX_LANGUAGE=(os.getenv("VOICEBOX_LANGUAGE") or "ru").strip() or "ru",
        VOICEBOX_INSTRUCT=(
            os.getenv("VOICEBOX_INSTRUCT")
            or (
                "Warm natural prayerful speech, gentle rhythm, slight emotional variation, "
                "not monotone and not robotic. Soft unhurried pace. "
                "The final word амИнь: stress on capital И (a-MÍN), clear and solemn."
            )
        ).strip(),
        VOICEBOX_ATEMPO=_parse_voicebox_atempo(os.getenv("VOICEBOX_ATEMPO")),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        MEDIA_INBOUND_ARCHIVE_DIR=media_raw,
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
