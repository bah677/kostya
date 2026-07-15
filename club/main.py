#!/usr/bin/env python3
"""
Главный файл запуска Telegram бота.
"""

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from config import config

if config.BOT_VARIANT == "nastya":
    from bot.core_nastya import TelegramBotNastya as TelegramBot
else:
    from bot.core import TelegramBot

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "log"
LOG_ARC_DIR = LOG_DIR / "arc"
ACTIVE_LOG_PATH = LOG_DIR / "bot.log"
ACTIVE_ERROR_LOG_PATH = LOG_DIR / "bot-errors.log"
BOT_LOG_LINE_ROTATE_AFTER = 10_000
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class _DowngradeTransientPollingErrors(logging.Filter):
    """Понижает ERROR → WARNING для типичных сетевых сбоев long polling (не пишет в bot-errors.log)."""

    _SUBSTRINGS = (
        "ServerDisconnectedError",
        "Request timeout",
        "Bad Gateway",
        "Gateway Timeout",
        "Connection reset",
        "Connection aborted",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.ERROR or record.name != "aiogram.dispatcher":
            return True
        msg = record.getMessage()
        if "Failed to fetch updates" not in msg:
            return True
        if any(s in msg for s in self._SUBSTRINGS):
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


def _log_file_has_more_than(path: Path, max_lines: int) -> bool:
    """Быстро выясняем, нужна ли ротация: True, если строк строго больше max_lines."""
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
    """
    Переносит текущий активный лог в ``log/arc/{prefix}_YYYYMMDD_HHMMSS.log``.
    Вызывается при старте процесса (ротация bot.log / bot-errors.log).
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_ARC_DIR.mkdir(parents=True, exist_ok=True)
    if not active_path.is_file():
        return
    try:
        if active_path.stat().st_size == 0:
            active_path.unlink(missing_ok=True)
            return
    except OSError:
        return

    slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = LOG_ARC_DIR / f"{archive_prefix}_{slug}.log"
    n = 0
    while archived.exists():
        n += 1
        archived = LOG_ARC_DIR / f"{archive_prefix}_{slug}_{n}.log"
    active_path.rename(archived)


def _prepare_logs_on_startup() -> None:
    """bot-errors.log — новый файл на каждый запуск; bot.log — только если >10k строк."""
    if _log_file_has_more_than(ACTIVE_LOG_PATH, BOT_LOG_LINE_ROTATE_AFTER):
        _archive_active_log(ACTIVE_LOG_PATH, "bot")
    _archive_active_log(ACTIVE_ERROR_LOG_PATH, "bot-errors")


_prepare_logs_on_startup()

# Настройка логирования
_error_file_handler = logging.FileHandler(ACTIVE_ERROR_LOG_PATH, encoding="utf-8")
_error_file_handler.setLevel(logging.ERROR)
_error_file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ACTIVE_LOG_PATH, encoding="utf-8"),
        _error_file_handler,
    ],
)

_poll_noise = _DowngradeTransientPollingErrors()
for _h in logging.root.handlers:
    _h.addFilter(_poll_noise)

logger = logging.getLogger(__name__)


class BotRunner:
    """Управление жизненным циклом бота."""

    def __init__(self):
        self.bot = None
        self.shutdown_event = asyncio.Event()
        self._stop_requested = False

    async def initialize(self) -> None:
        """Инициализация бота."""
        try:
            logger.info("Инициализация Telegram бота...")

            self.bot = TelegramBot()
            await self.bot.initialize()

            logger.info("Бот инициализирован успешно")
        except Exception as e:
            logger.error(f"Ошибка инициализации бота: {e}")
            raise

    async def start(self) -> None:
        """Запуск бота."""
        if not self.bot:
            await self.initialize()

        logger.warning("Запуск бота...")
        try:
            await self.bot.start()
        except asyncio.CancelledError:
            logger.info("Задача бота отменена")
            raise
        except Exception as e:
            logger.error(f"Ошибка в работе бота: {e}")
            raise

    async def stop(self) -> None:
        """Остановка бота."""
        if self._stop_requested:
            return

        self._stop_requested = True
        logger.info("Остановка бота...")

        if self.bot:
            await self.bot.close()
            logger.warning("Бот остановлен")

        self.shutdown_event.set()


@asynccontextmanager
async def bot_lifecycle():
    """Контекстный менеджер для управления жизненным циклом бота."""
    runner = BotRunner()

    # Создаём задачу для обработки остановки
    stop_future = None

    def signal_handler(signum, frame):
        """Обработчик сигналов - работает синхронно"""
        nonlocal stop_future
        logger.info(f"Получен сигнал {signum}, завершение работы...")
        if stop_future is None:
            # Запускаем остановку в event loop
            stop_future = asyncio.create_task(runner.stop())

    # Регистрация обработчиков сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await runner.initialize()
        yield runner
    finally:
        # Гарантируем остановку даже при ошибках
        if not runner.shutdown_event.is_set():
            await runner.stop()


async def main() -> None:
    """Главная функция запуска."""
    logger.warning("Запуск Bot...")

    runner = None
    bot_task = None

    try:
        async with bot_lifecycle() as runner:
            # Запуск бота в отдельной задаче
            bot_task = asyncio.create_task(runner.start())

            # Создаём задачу для ожидания shutdown_event
            shutdown_waiter = asyncio.create_task(runner.shutdown_event.wait())

            # Ожидаем либо завершение бота, либо shutdown_event
            done, pending = await asyncio.wait(
                [bot_task, shutdown_waiter],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Отменяем pending задачи
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except asyncio.CancelledError:
        logger.info("Основная задача отменена")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise
    finally:
        # Гарантируем остановку
        if runner and not runner.shutdown_event.is_set():
            await runner.stop()

        # Дополнительная задержка для завершения операций
        await asyncio.sleep(0.5)

        logger.warning("Бот полностью остановлен")


if __name__ == "__main__":
    try:
        # Валидация конфигурации
        config._validate_required()

        # Запуск главной функции
        asyncio.run(main())

    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        logger.info("Проверьте файл .env и необходимые переменные окружения")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("Бот остановлен пользователем")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")
        sys.exit(1)
