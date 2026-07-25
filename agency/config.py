"""Конфиг agency (dotenv)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class DbDsn:
    host: str
    port: int
    name: str
    user: str
    password: str

    def as_asyncpg(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.name,
            "user": self.user,
            "password": self.password,
        }


def _dsn(prefix: str, default_name: str) -> DbDsn:
    return DbDsn(
        host=os.getenv(f"{prefix}_HOST", "localhost") or "localhost",
        port=_int(f"{prefix}_PORT", 5432),
        name=os.getenv(f"{prefix}_NAME", default_name) or default_name,
        user=os.getenv(f"{prefix}_USER", "") or "",
        password=os.getenv(f"{prefix}_PASSWORD", "") or "",
    )


@dataclass
class Config:
    # Agency own DB (RW)
    AGENCY_DB: DbDsn
    # Ecosystem sources (RO)
    BIBLIA_DB: DbDsn
    CLUB_DB: DbDsn

    AGENCY_BOT_TOKEN: str = ""
    #: Супер-админ: полный доступ + /admin_add|/admin_del|/admins
    SUPER_ADMIN_ID: int = 0
    #: Запасной bootstrap-список админов из .env (через запятую)
    AGENCY_ADMIN_IDS: tuple = ()
    AGENCY_BRIEF_CHAT_ID: int = 0

    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_STRONG_MODEL: str = "gpt-4o"
    OPENAI_WEB_MODEL: str = "gpt-4o-mini-search-preview"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"

    RAG_ENABLED: bool = False
    RAG_CHROMA_PERSIST_DIR: str = ""
    RAG_EXPERT_COLLECTION: str = "expert_materials"
    RAG_GOLDEN_COLLECTION: str = "golden_examples"
    RAG_EMBEDDING_MODEL: str = "text-embedding-3-small"

    CRON_HOUR_MSK: int = 3
    CRON_MINUTE_MSK: int = 0
    DIALOG_SAMPLE_LIMIT: int = 80
    ENABLE_DRAFT_PR: bool = False
    GITHUB_BIBLIA_PATH: str = str(ROOT.parent / "biblia")

    # QA Manager — корни error-логов (через запятую; пусто = дефолты prod)
    QA_LOG_ROOTS_CLUB: tuple = ()
    QA_LOG_ROOTS_BIBLIA: tuple = ()
    QA_LOG_ROOTS_AVATAR: tuple = ()
    QA_TOP_CLUSTERS: int = 12
    QA_DIGEST_MAX_CHARS: int = 28000

    TIMEZONE: str = "Europe/Moscow"


def _paths(name: str) -> tuple:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.replace(";", ",").split(",") if p.strip())


def load_config() -> Config:
    admin_raw = (os.getenv("AGENCY_ADMIN_IDS") or "").strip()
    admins: list[int] = []
    for part in admin_raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            admins.append(int(part))

    return Config(
        AGENCY_DB=_dsn("AGENCY_DB", "agency"),
        BIBLIA_DB=_dsn("BIBLIA_DB", "biblia_bot"),
        CLUB_DB=_dsn("CLUB_DB", "club_db"),
        AGENCY_BOT_TOKEN=(os.getenv("AGENCY_BOT_TOKEN") or "").strip(),
        SUPER_ADMIN_ID=_int("SUPER_ADMIN_ID", 0),
        AGENCY_ADMIN_IDS=tuple(admins),
        AGENCY_BRIEF_CHAT_ID=_int("AGENCY_BRIEF_CHAT_ID", 0),
        OPENAI_API_KEY=(os.getenv("OPENAI_API_KEY") or "").strip(),
        DEEPSEEK_API_KEY=(os.getenv("DEEPSEEK_API_KEY") or "").strip(),
        ANTHROPIC_API_KEY=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        OPENAI_STRONG_MODEL=os.getenv("OPENAI_STRONG_MODEL", "gpt-4o") or "gpt-4o",
        OPENAI_WEB_MODEL=os.getenv("OPENAI_WEB_MODEL", "gpt-4o-mini-search-preview")
        or "gpt-4o-mini-search-preview",
        DEEPSEEK_MODEL=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        or "deepseek-v4-flash",
        ANTHROPIC_MODEL=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        or "claude-sonnet-4-5",
        RAG_ENABLED=_bool("RAG_ENABLED", False),
        RAG_CHROMA_PERSIST_DIR=(os.getenv("RAG_CHROMA_PERSIST_DIR") or "").strip(),
        RAG_EXPERT_COLLECTION=os.getenv("RAG_EXPERT_COLLECTION", "expert_materials")
        or "expert_materials",
        RAG_GOLDEN_COLLECTION=os.getenv("RAG_GOLDEN_COLLECTION", "golden_examples")
        or "golden_examples",
        RAG_EMBEDDING_MODEL=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
        or "text-embedding-3-small",
        CRON_HOUR_MSK=_int("CRON_HOUR_MSK", 3),
        CRON_MINUTE_MSK=_int("CRON_MINUTE_MSK", 0),
        DIALOG_SAMPLE_LIMIT=_int("DIALOG_SAMPLE_LIMIT", 80),
        ENABLE_DRAFT_PR=_bool("ENABLE_DRAFT_PR", False),
        GITHUB_BIBLIA_PATH=os.getenv(
            "GITHUB_BIBLIA_PATH", str(ROOT.parent / "biblia")
        )
        or str(ROOT.parent / "biblia"),
        QA_LOG_ROOTS_CLUB=_paths("QA_LOG_ROOTS_CLUB"),
        QA_LOG_ROOTS_BIBLIA=_paths("QA_LOG_ROOTS_BIBLIA"),
        QA_LOG_ROOTS_AVATAR=_paths("QA_LOG_ROOTS_AVATAR"),
        QA_TOP_CLUSTERS=_int("QA_TOP_CLUSTERS", 12),
        QA_DIGEST_MAX_CHARS=_int("QA_DIGEST_MAX_CHARS", 28000),
    )


config = load_config()
