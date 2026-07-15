"""
Скачивание файлов из Telegram во временную директорию.
"""

import os
import tempfile
import logging
import asyncio
from typing import Optional
from datetime import datetime, timedelta

from aiogram import Bot

from .config.settings import TEMP_FILE_CONFIG

logger = logging.getLogger(__name__)


class FileDownloader:
    """Скачивает файлы из Telegram во временное хранилище"""
    
    def __init__(self):
        self.base_dir = TEMP_FILE_CONFIG['base_dir']
        self._ensure_temp_dir()
    
    def _ensure_temp_dir(self):
        """Создает временную директорию если её нет"""
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"📁 Временная директория: {self.base_dir}")
    
    def _get_file_extension(self, file_path: Optional[str]) -> str:
        """Получает расширение файла"""
        if file_path and '.' in file_path:
            return os.path.splitext(file_path)[1]
        return '.tmp'
    
    async def download_file(self, file_id: str, bot: Bot) -> Optional[str]:
        """
        Скачивает файл по file_id во временную директорию.
        
        Args:
            file_id: Telegram file_id
            bot: экземпляр бота для скачивания
            
        Returns:
            путь к скачанному файлу или None
        """
        try:
            # Получаем объект файла
            file = await bot.get_file(file_id)
            
            # Определяем расширение
            suffix = self._get_file_extension(file.file_path)
            
            # Создаем временный файл
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                dir=self.base_dir,
                delete=False
            ) as tmp_file:
                file_path = tmp_file.name
            
            # Скачиваем
            await bot.download_file(file.file_path, file_path)
            
            logger.debug(f"✅ Файл скачан: {file_path} ({file.file_size} bytes)")
            return file_path
            
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания файла {file_id}: {e}")
            return None
    
    async def cleanup_file(self, file_path: str):
        """
        Удаляет временный файл.
        
        Args:
            file_path: путь к файлу
        """
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"✅ Временный файл удален: {file_path}")
        except Exception as e:
            logger.error(f"❌ Ошибка удаления файла {file_path}: {e}")
    
    async def cleanup_old_files(self, hours: int = None):
        """
        Удаляет старые временные файлы.
        
        Args:
            hours: старше скольких часов удалять
        """
        if hours is None:
            hours = TEMP_FILE_CONFIG['cleanup_age_hours']
        
        try:
            now = datetime.now()
            cutoff = now - timedelta(hours=hours)
            
            for filename in os.listdir(self.base_dir):
                file_path = os.path.join(self.base_dir, filename)
                if os.path.isfile(file_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if mtime < cutoff:
                        os.unlink(file_path)
                        logger.debug(f"✅ Удален старый файл: {filename}")
            
            logger.info(f"🧹 Очистка временных файлов завершена")
        except Exception as e:
            logger.error(f"❌ Ошибка очистки временных файлов: {e}")