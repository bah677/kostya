#!/usr/bin/env python3
"""
Точка входа автономного проекта «БиблияБот» (каталог = корень процесса).

Переменные: validate_biblia_bot_startup / load_biblia_bot_config в config.py —
BIBLIA_BOT_TOKEN, BIBLIA_DB_NAME, Postgres (DB_*), OPENAI_API_KEY, DEEPSEEK_API_KEY.
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from bot_app import BotApplication
from config import load_biblia_bot_config, validate_biblia_bot_startup

_PROJECT_ROOT = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_ROOT / "log"
_ACTIVE_LOG = _LOG_DIR / "biblia_bot.log"
_ERROR_LOG = _LOG_DIR / "biblia_bot_errors.log"
_MAIN_LOG_MAX_LINES = 10_000


def _rotate_on_startup(path: Path) -> None:
    """Если файл уже есть, переименовать с меткой времени (mtime).

    Имитирует ротацию обычных логов: <name>-YYYYMMDD_HHMMSS<ext>.
    """
    if not path.exists():
        return
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        mtime = datetime.now()
    suffix = mtime.strftime("%Y%m%d_%H%M%S")
    rotated = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
    counter = 1
    while rotated.exists():
        rotated = path.with_name(f"{path.stem}-{suffix}_{counter}{path.suffix}")
        counter += 1
    path.rename(rotated)


def _main_log_exceeds_line_limit(path: Path, *, max_lines: int = _MAIN_LOG_MAX_LINES) -> bool:
    """True, если в файле больше max_lines строк (эффективно: читаем до max_lines+1)."""
    if not path.exists():
        return False
    try:
        with path.open("rb") as f:
            for i, _ in enumerate(f, start=1):
                if i > max_lines:
                    return True
        return False
    except OSError:
        return False


def _setup_logging() -> None:
    bc = load_biblia_bot_config()
    lvl = getattr(logging, str(bc.LOG_LEVEL).upper(), logging.INFO)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    _rotate_on_startup(_ERROR_LOG)
    if _main_log_exceeds_line_limit(_ACTIVE_LOG):
        _rotate_on_startup(_ACTIVE_LOG)

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    root = logging.getLogger()
    root.setLevel(lvl)
    for h in list(root.handlers):
        root.removeHandler(h)

    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setFormatter(fmt)
    root.addHandler(stdout_h)

    main_fh = logging.FileHandler(_ACTIVE_LOG, encoding="utf-8")
    main_fh.setFormatter(fmt)
    root.addHandler(main_fh)

    err_fh = logging.FileHandler(_ERROR_LOG, encoding="utf-8")
    err_fh.setLevel(logging.ERROR)
    err_fh.setFormatter(fmt)
    root.addHandler(err_fh)


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)
    bc = load_biblia_bot_config()
    bot = BotApplication(biblia_cfg=bc)
    try:
        await bot.initialize()
        logger.warning("Biblia: polling...")
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
