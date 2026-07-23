#!/usr/bin/env python3
"""
Точка входа avatar_kostya (каталог = корень процесса).

Логи — как в club: bot.log / bot-errors.log, ротация в log/arc при старте;
архивы старше 30 дней удаляются.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from bot_app import BotApplication
from config import load_biblia_bot_config, validate_biblia_bot_startup

_PROJECT_ROOT = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_ROOT / "log"
_ARC_DIR = _LOG_DIR / "arc"
_BOT_LOG = _LOG_DIR / "bot.log"
_ERR_LOG = _LOG_DIR / "bot-errors.log"

_LOG_ROTATE_LINES = 10_000
_LOG_ARC_KEEP_DAYS = 30


def _log_file_has_more_than(path: Path, max_lines: int) -> bool:
    try:
        n = 0
        with path.open("rb") as fh:
            for _ in fh:
                n += 1
                if n > max_lines:
                    return True
        return False
    except OSError:
        return False


def _archive_active_log(active_path: Path, archive_prefix: str) -> None:
    """Переносит активный лог в ``log/arc/{prefix}_YYYYMMDD_HHMMSS.log``."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _ARC_DIR.mkdir(parents=True, exist_ok=True)
    if not active_path.is_file():
        return
    try:
        if active_path.stat().st_size == 0:
            active_path.unlink(missing_ok=True)
            return
    except OSError:
        return

    slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = _ARC_DIR / f"{archive_prefix}_{slug}.log"
    n = 0
    while archived.exists():
        n += 1
        archived = _ARC_DIR / f"{archive_prefix}_{slug}_{n}.log"
    active_path.rename(archived)


def _prune_log_archives(keep_days: int = _LOG_ARC_KEEP_DAYS) -> None:
    """Удаляет файлы в log/arc и ротированные в log/ старше keep_days."""
    cutoff = time.time() - keep_days * 86400
    roots = [_ARC_DIR] if _ARC_DIR.is_dir() else []
    for root in roots:
        for p in root.iterdir():
            if not p.is_file():
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
    if not _LOG_DIR.is_dir():
        return
    keep_names = {_BOT_LOG.name, _ERR_LOG.name, "err.log"}
    for p in _LOG_DIR.iterdir():
        if not p.is_file() or p.name in keep_names:
            continue
        # только явные архивы / ротации, не трогаем произвольные файлы без даты
        if not (
            p.suffix == ".gz"
            or "-20" in p.name
            or "_20" in p.name
            or p.name.endswith((".log.1", ".log.2", ".log.3", ".log.4", ".log.5"))
        ):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def _prepare_logs_on_startup() -> None:
    """bot-errors — новый файл на каждый запуск; bot.log — если >10k строк."""
    # совместимость со старым err.log
    legacy_err = _LOG_DIR / "err.log"
    if legacy_err.is_file() and not _ERR_LOG.exists():
        try:
            legacy_err.rename(_ERR_LOG)
        except OSError:
            pass

    if _log_file_has_more_than(_BOT_LOG, _LOG_ROTATE_LINES):
        _archive_active_log(_BOT_LOG, "bot")
    _archive_active_log(_ERR_LOG, "bot-errors")
    _prune_log_archives()


def _setup_logging() -> None:
    bc = load_biblia_bot_config()
    log_lvl_name = (os.getenv("LOG_LEVEL") or str(bc.LOG_LEVEL) or "INFO").strip().upper()
    rag_debug = (os.getenv("RAG_INDEXER_DEBUG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ) or log_lvl_name == "DEBUG"
    lvl = getattr(logging, str(bc.LOG_LEVEL).upper(), logging.INFO)
    if rag_debug:
        lvl = logging.DEBUG

    _prepare_logs_on_startup()

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    bot_file_handler = logging.FileHandler(_BOT_LOG, encoding="utf-8")
    bot_file_handler.setFormatter(fmt)

    err_file_handler = logging.FileHandler(_ERR_LOG, encoding="utf-8")
    err_file_handler.setLevel(logging.ERROR)
    err_file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    logging.basicConfig(
        level=lvl,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[console_handler, bot_file_handler, err_file_handler],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if rag_debug:
        logging.getLogger("aiogram").setLevel(logging.INFO)
        logging.getLogger("aiogram.dispatcher").setLevel(logging.INFO)
        logging.getLogger("aiogram.client.session").setLevel(logging.WARNING)
        logging.getLogger("aiogram.event").setLevel(logging.INFO)
        logging.getLogger(__name__).warning(
            "Подробный режим логов: корень DEBUG (RAG_INDEXER_DEBUG=1 или LOG_LEVEL=DEBUG); "
            "aiogram приглушён до INFO/WARNING."
        )


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)
    bc = load_biblia_bot_config()
    bot = BotApplication(biblia_cfg=bc)
    try:
        await bot.initialize()
        logger.warning("avatar_kostya: polling...")
        await bot.start()
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        validate_biblia_bot_startup(load_biblia_bot_config())
    except ValueError as e:
        logging.basicConfig(level=logging.INFO)
        log = logging.getLogger(__name__)
        log.error("%s", e)
        log.info(
            "Нужно в .env: BIBLIA_BOT_TOKEN, BIBLIA_DB_NAME, DB_*, OPENAI_API_KEY, DEEPSEEK_API_KEY"
        )
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
