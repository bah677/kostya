#!/usr/bin/env python3
"""Запуск внутреннего DM viewer для reports.mironbot.ru/club-dm/."""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="DM viewer club+biblia (reports)")
    parser.add_argument(
        "--club-env",
        default="/home/appuser/club/.env",
        help="Путь к .env клубной БД",
    )
    parser.add_argument(
        "--biblia-env",
        default="/home/appuser/biblia/.env",
        help="Путь к .env БиблияБота",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from services.dm_viewer.app import create_app, dsn_from_env_file

    club_dsn = dsn_from_env_file(args.club_env)
    biblia_dsn = dsn_from_env_file(args.biblia_env)
    app = create_app(club_dsn=club_dsn, biblia_dsn=biblia_dsn)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
