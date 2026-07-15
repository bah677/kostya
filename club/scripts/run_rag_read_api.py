#!/usr/bin/env python3
"""Запуск read-only RAG HTTP API (вариант C)."""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn

from config import config as default_config
from services.rag_read_api.app import create_app


def _load_runtime_config(env_file: str | None):
    if not env_file:
        return default_config
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    from config import load_config

    return load_config()


def main() -> None:
    parser = argparse.ArgumentParser(description="Club RAG read-only HTTP API")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Путь к .env (например /home/appuser/club/.env)",
    )
    parser.add_argument("--host", default=None, help="Переопределить RAG_READ_API_HOST")
    parser.add_argument("--port", type=int, default=None, help="Переопределить RAG_READ_API_PORT")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runtime_cfg = _load_runtime_config(args.env_file)
    token = (runtime_cfg.RAG_READ_API_TOKEN or "").strip()
    if not token:
        raise SystemExit("RAG_READ_API_TOKEN не задан в .env")
    if not runtime_cfg.RAG_ENABLED:
        raise SystemExit("RAG_ENABLED=0 — включите RAG перед запуском API")

    host = args.host or runtime_cfg.RAG_READ_API_HOST
    port = args.port or runtime_cfg.RAG_READ_API_PORT
    app = create_app(cfg=runtime_cfg)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
