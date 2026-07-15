"""Одноразовый вызов DeepSeek для заключения по отчёту об отвале."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from bot.texts.prompts.churn_analysis import (
    CHURN_ANALYSIS_SYSTEM,
    churn_analysis_user_content,
)

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-chat"


async def analyze_churn_with_deepseek(
    *,
    api_key: str,
    about_club_text: str,
    churn_data_json: str,
    model: str = DEFAULT_MODEL,
    timeout_sec: float = 180.0,
) -> Optional[str]:
    """Возвращает текст заключения или None при ошибке."""

    user_content = churn_analysis_user_content(
        about_club_text=about_club_text,
        churn_data_json=churn_data_json,
    )

    payload = {
        "model": model,
        "temperature": 0.35,
        "messages": [
            {"role": "system", "content": CHURN_ANALYSIS_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices") or []
        if not choices:
            logger.warning("deepseek: empty choices: %s", data)
            return None
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        return content or None
    except httpx.HTTPStatusError as e:
        logger.warning(
            "deepseek HTTP %s: %s",
            e.response.status_code,
            (e.response.text or "")[:500],
        )
        return None
    except Exception as e:
        logger.warning("deepseek request failed: %s", e)
        return None
