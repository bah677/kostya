# bot/features/license_service.py
import logging
from typing import Dict, Any, Optional
from config import config

logger = logging.getLogger(__name__)


class AccessResult:
    """Результат проверки доступа"""
    
    def __init__(self, has_access: bool, reason: str = "", details: Dict[str, Any] = None):
        self.has_access = has_access
        self.reason = reason
        self.details = details or {}
    
    @property
    def is_denied(self) -> bool:
        return not self.has_access


class LicenseService:
    """Сервис проверки доступа (бан/не бан)"""
    
    def __init__(self, user_storage):
        self.user_storage = user_storage

    async def check_access(self, user_id: int) -> bool:
        """
        Простая проверка доступа: не забанен ли пользователь
        """
        try:
            user_data = await self.user_storage.get_user(user_id)
            
            # Если пользователя нет в БД - даем доступ (создастся при онбординге)
            if not user_data:
                logger.info(f"🆕 New user {user_id}, access granted")
                return True
            
            # Проверяем бан
            is_banned = user_data.get('is_banned', False)
            
            if is_banned:
                logger.info(f"🚫 User {user_id} is banned, access denied")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error checking access for user_id={user_id}: {e}")
            return False    
    
    async def can_ask_question(self, user_id: int) -> AccessResult:
        """
        Проверяет, может ли пользователь задать вопрос.
        Теперь просто проверяет бан.
        """
        try:
            # Проверяем бан
            user_data = await self.user_storage.get_user(user_id)
            is_banned = user_data.get('is_banned', False) if user_data else False
            
            if is_banned:
                return AccessResult(
                    has_access=False,
                    reason="user_banned",
                    details={"type": "denied", "reason": "banned", "user_id": user_id}
                )
            
            # Все не забаненные имеют доступ
            return AccessResult(
                has_access=True,
                reason="access_granted",
                details={"type": "granted", "user_id": user_id}
            )
            
        except Exception as e:
            logger.error(f"❌ Error checking question access for user_id={user_id}: {e}")
            return AccessResult(
                has_access=False,
                reason="error",
                details={"error": str(e), "user_id": user_id}
            )
    
    async def get_access_info(self, user_id: int) -> Dict[str, Any]:
        """Получает полную информацию о доступе пользователя"""
        try:
            user_data = await self.user_storage.get_user(user_id)
            is_banned = user_data.get('is_banned', False) if user_data else False

            return {
                "has_license": not is_banned,
                "is_in_test_period": not is_banned,
                "questions": {
                    "asked": 0,
                    "remaining": 999999,
                    "limit": 999999,
                },
                "is_banned": is_banned,
            }

        except Exception as e:
            logger.error(f"❌ Error getting access info for user_id={user_id}: {e}")
            return {"error": str(e)}