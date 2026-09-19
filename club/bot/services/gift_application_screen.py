"""Проверка заявки моделью (АНК-3)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from bot.utils.admin_channel import send_admin_html_message
from config import config

logger = logging.getLogger(__name__)


def _gift_admin_thread_id() -> Optional[int]:
    tid = int(getattr(config, "GIFT_CAMPAIGN_ADMIN_TOPIC_ID", 0) or 0)
    return tid if tid > 0 else None


SCREEN_PROMPT = """\
Ты помогаешь отобрать участников в закрытое христианское
сообщество «Любящие Бога». Перед тобой заявка человека,
который хочет получить месяц участия в подарок.

Верни ТОЛЬКО JSON:
{{
  "verdict": "pass" | "reject" | "review",
  "reason": "одно предложение по-русски",
  "flags": ["hostility" | "ads" | "nonsense" | "crisis"]
}}

reject — ТОЛЬКО если есть хотя бы одно:
- враждебность, оскорбления или насмешка в адрес ведущего,
  клуба, его участников, веры или Бога;
- реклама, ссылки, предложение услуг или заработка,
  приглашение в другие сообщества;
- ответы без смысла, набор символов, издёвка.

НЕ являются причиной отказа, даже если звучат тяжело:
- развод, измена, предательство, обман со стороны близких;
- болезнь, в том числе тяжёлая; утрата; одиночество;
- долги, безработица, страх, тревога, усталость;
- сомнения в вере, гнев на жизненные обстоятельства;
- принадлежность к другой христианской конфессии
  или отсутствие церковного опыта;
- короткий или неграмотный ответ.
Такие люди — главная аудитория клуба.

Если сомневаешься между pass и reject — выбирай review.

Ответ 1 (о себе): {q1_about}
Ответ 2 (что происходит): {q2_why}
"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")
_ALLOWED_FLAGS = frozenset({"hostility", "ads", "nonsense", "crisis"})
_ALLOWED_VERDICTS = frozenset({"pass", "reject", "review"})


def _parse_screen_json(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    mo = _JSON_RE.search(text)
    if not mo:
        return None
    try:
        data = json.loads(mo.group(0))
    except Exception:
        return None
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in _ALLOWED_VERDICTS:
        return None
    reason = str(data.get("reason") or "").strip()[:500]
    flags_raw = data.get("flags") or []
    flags: List[str] = []
    if isinstance(flags_raw, list):
        for f in flags_raw:
            fs = str(f).strip().lower()
            if fs in _ALLOWED_FLAGS and fs not in flags:
                flags.append(fs)
    return {"verdict": verdict, "reason": reason, "flags": flags}


async def _call_screen_llm(q1: str, q2: str) -> Optional[Dict[str, Any]]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        logger.warning("gift screen: no DEEPSEEK_API_KEY")
        return None
    model = getattr(config, "CHAT_MODEL", None) or "deepseek-v4-flash"
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
        timeout=45.0,
        max_retries=0,
    )
    prompt = SCREEN_PROMPT.format(
        q1_about=(q1 or "")[:2000],
        q2_why=(q2 or "")[:2000],
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
        raw = (response.choices[0].message.content or "").strip()
        return _parse_screen_json(raw)
    except Exception as e:
        logger.warning("gift screen LLM failed: %s", e)
        return None


async def screen_gift_application(
    *,
    user_storage,
    bot,
    application_id: int,
) -> Dict[str, Any]:
    app = await user_storage.get_gift_application_by_id(application_id)
    if not app:
        return {"ok": False, "reason": "missing"}
    if app.get("status") not in ("submitted",):
        return {"ok": False, "reason": "bad_status", "status": app.get("status")}

    parsed: Optional[Dict[str, Any]] = None
    for _attempt in range(3):
        parsed = await _call_screen_llm(app.get("q1_about") or "", app.get("q2_why") or "")
        if parsed:
            break

    if not parsed:
        parsed = {
            "verdict": "review",
            "reason": "модель недоступна или вернула невалидный ответ",
            "flags": [],
        }

    verdict = parsed["verdict"]
    flags = parsed.get("flags") or []
    reason = parsed.get("reason") or ""

    # crisis не влияет на verdict, только уведомление
    if "crisis" in flags and bot:
        try:
            await send_admin_html_message(
                bot,
                (
                    f"⚠️ <b>Заявка gift #{application_id}</b> — флаг crisis\n"
                    f"user_id={app['user_id']}\n"
                    f"{reason or '—'}"
                ),
                thread_id=_gift_admin_thread_id(),
            )
        except Exception:
            pass

    status = None
    if verdict == "pass":
        status = "screened"  # score поставит queued
    elif verdict == "reject":
        status = "rejected"
    else:
        status = "submitted"

    await user_storage.set_gift_application_verdict(
        application_id,
        verdict=verdict,
        reason=reason,
        status=status,
    )

    try:
        await user_storage.log_interaction(
            user_id=int(app["user_id"]),
            event_category="gift_application",
            event_type="gift_app_screened",
            data={
                "application_id": application_id,
                "verdict": verdict,
                "flags": flags,
                "reason": reason,
            },
            source="gift_application",
            outcome="success",
        )
    except Exception:
        pass

    if verdict == "review" and bot:
        try:
            await send_admin_html_message(
                bot,
                (
                    f"🔎 <b>Заявка на проверку #{application_id}</b>\n"
                    f"user_id={app['user_id']}\n"
                    f"Причина модели: {reason or '—'}\n"
                    f"/gift_campaign review"
                ),
                thread_id=_gift_admin_thread_id(),
            )
        except Exception:
            pass

    return {"ok": True, "verdict": verdict, "flags": flags, "reason": reason}
