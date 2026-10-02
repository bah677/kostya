#!/usr/bin/env python3
"""Live-отчёт розыгрыша для reports.mironbot.ru (кнопка «Обновить снимок»).

  ./venv/bin/python3 scripts/run_gift_campaign_report.py --port 8795
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from build_gift_campaign_report import (  # noqa: E402
    DEFAULT_OUT,
    collect,
    render,
)

MSK = ZoneInfo("Europe/Moscow")
logger = logging.getLogger("gift_campaign_report")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"

_lock = asyncio.Lock()
_cache_html: Optional[str] = None
_cache_meta: Dict[str, Any] = {}


async def _rebuild(*, persist: bool = True) -> Dict[str, Any]:
    global _cache_html, _cache_meta
    async with _lock:
        data = await collect()
        html_out = render(data, live=True)
        _cache_html = html_out
        gen = data["generated_at"]
        assert isinstance(gen, datetime)
        _cache_meta = {
            "ok": True,
            "generated_at": gen.isoformat(timespec="seconds"),
            "generated_at_msk": gen.strftime("%d.%m.%Y %H:%M МСК"),
        }
        if persist:
            DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_OUT.write_text(html_out, encoding="utf-8")
            meta_path = DEFAULT_OUT.parent / "meta.json"
            meta_path.write_text(
                __import__("json").dumps(_cache_meta, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
        logger.info("gift report rebuilt at %s", _cache_meta["generated_at_msk"])
        return dict(_cache_meta)


class _FeatureBag:
    def __init__(self, club_group) -> None:
        self._club_group = club_group

    def get(self, name: str):
        if name == "club_group":
            return self._club_group
        raise KeyError(name)


async def _run_draw_action() -> Dict[str, Any]:
    """Тот же сценарий, что кнопка в админ-топике: draw или grant."""
    load_dotenv(DEFAULT_PROD_ENV, override=True)
    from aiogram import Bot

    from bot.features.club_group import ClubGroupFeature
    from bot.services.gift_application_alerts import resolve_campaign_wave_button
    from bot.services.gift_application_select import (
        ensure_campaign_wave,
        select_applications_for_wave,
    )
    from bot.services.gift_wave_service import grant_wave_batch
    from bot.texts import ru_gift_application as ga_txt
    from config import load_config
    from storage.user_storage import UserStorage

    cfg = load_config()
    storage = UserStorage(cfg.database_url)
    await storage.connect()
    bot = Bot(token=cfg.MIRON_BOT_TOKEN)
    try:
        btn = await resolve_campaign_wave_button(storage)
        if not btn:
            # почему недоступно — для понятного ответа на странице
            from bot.services.gift_application_eligibility import count_remaining_tickets

            left = await count_remaining_tickets(storage)
            by_src = await storage.count_queued_by_source()
            ready = sum(int(v) for v in (by_src or {}).values())
            async with storage.get_connection() as conn:
                review = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int FROM gift_application
                    WHERE campaign = 'gift-2026-09'
                      AND status = 'submitted'
                      AND COALESCE(verdict, '') = 'review'
                    """
                )
            reasons = []
            if left <= 0:
                reasons.append("нет свободных слотов билетов")
            if ready <= 0:
                reasons.append(
                    f"нет анкет в статусе queued+pass (на проверке сейчас {int(review or 0)})"
                )
            elif ready < 15:
                reasons.append(f"мало готовых анкет к розыгрышу ({ready})")
            if not reasons:
                reasons.append("кампания на паузе или завершена")
            return {
                "ok": False,
                "error": "Сейчас розыгрыш недоступен: " + "; ".join(reasons) + ".",
            }

        idx = int(btn["wave_index"])
        mode = str(btn.get("mode") or "draw")
        wave = await ensure_campaign_wave(storage, wave_index=idx)
        if not wave:
            return {"ok": False, "error": f"Не удалось создать/найти волну {idx}"}
        wid = int(wave["id"])

        club_group = ClubGroupFeature(user_storage=storage, bot=bot)
        fm = _FeatureBag(club_group)

        select_res = None
        if mode == "draw":
            select_res = await select_applications_for_wave(
                storage, wave_id=wid, wave_index=idx
            )
        await storage.set_gift_wave_status(wid, "running")
        grant_res = await grant_wave_batch(
            user_storage=storage,
            bot=bot,
            feature_manager=fm,
            wave_id=wid,
        )
        html_msg = ga_txt.format_wave_admin_result_html(
            wave_index=idx,
            mode=mode,
            select=select_res,
            grant=grant_res,
        )
        # короткий plaintext для JSON
        plain = (
            html_msg.replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")
            .replace("<br>", "\n")
        )
        import re

        plain = re.sub(r"<[^>]+>", "", plain)
        return {
            "ok": True,
            "mode": mode,
            "wave_index": idx,
            "wave_id": wid,
            "select": select_res,
            "grant": grant_res,
            "message": plain.strip().replace("\n", " · "),
            "html": html_msg,
        }
    finally:
        await bot.session.close()
        await storage.close()


def create_app() -> FastAPI:
    load_dotenv(DEFAULT_PROD_ENV, override=True)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.on_event("startup")
    async def _startup() -> None:
        try:
            await _rebuild(persist=True)
        except Exception:
            logger.exception("initial gift report build failed")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        if not _cache_html:
            await _rebuild(persist=True)
        return HTMLResponse(_cache_html or "<p>нет данных</p>")

    @app.get("/api/meta")
    async def meta() -> JSONResponse:
        if not _cache_meta:
            await _rebuild(persist=False)
        return JSONResponse(_cache_meta)

    @app.post("/api/refresh")
    async def refresh() -> JSONResponse:
        try:
            meta = await _rebuild(persist=True)
            return JSONResponse(meta)
        except Exception as e:
            logger.exception("refresh failed")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/draw")
    async def draw() -> JSONResponse:
        try:
            # не держим _lock на время Telegram-выдачи (минуты)
            result = await _run_draw_action()
        except Exception as e:
            logger.exception("draw failed")
            raise HTTPException(status_code=500, detail=str(e)) from e
        if not result.get("ok"):
            raise HTTPException(
                status_code=409,
                detail=result.get("error") or "розыгрыш недоступен",
            )
        try:
            await _rebuild(persist=True)
        except Exception:
            logger.exception("rebuild after draw failed")
        return JSONResponse(result)

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8795)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
