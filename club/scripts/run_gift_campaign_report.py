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
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from build_gift_campaign_report import (  # noqa: E402
    DEFAULT_OUT,
    collect,
    render,
)

MSK = ZoneInfo("Europe/Moscow")
logger = logging.getLogger("gift_campaign_report")

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


def create_app() -> FastAPI:
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
