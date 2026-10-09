#!/usr/bin/env python3
"""Одноразовая авторизация YouTube OAuth для автозагрузки молитв."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    # Нужен, чтобы бот мог оставлять комментарии под роликами
    # (commentThreads.insert). Загрузке видео не мешает.
    # Закрепить комментарий через API нельзя — такого метода в Data API v3 нет.
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_DEFAULT_SECRET = _ROOT / "data/youtube_prayer/youtube_client_secret.json"
_DEFAULT_TOKEN = _ROOT / "data/youtube_prayer/youtube_oauth_token.json"
_PENDING = _ROOT / "data/youtube_prayer/youtube_oauth_pending.json"


def _pick_redirect_uri(secret_path: Path) -> str:
    data = json.loads(secret_path.read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or {}
    uris = block.get("redirect_uris") or []
    for preferred in ("http://localhost", "http://127.0.0.1", "urn:ietf:wg:oauth:2.0:oob"):
        if preferred in uris:
            return preferred
    if uris:
        return str(uris[0])
    return "http://localhost"


def _extract_code(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if "code=" in text:
        parsed = urlparse(text)
        qs = parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        if code:
            return code
        m = re.search(r"[?&]code=([^&]+)", text)
        if m:
            return m.group(1)
    return text


def _make_flow(secret: Path):
    from google_auth_oauthlib.flow import InstalledAppFlow

    redirect_uri = _pick_redirect_uri(secret)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(secret),
        _SCOPES,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    return flow


def _save_pending(*, code_verifier: str, state: str) -> None:
    _PENDING.parent.mkdir(parents=True, exist_ok=True)
    _PENDING.write_text(
        json.dumps({"code_verifier": code_verifier, "state": state}, indent=2),
        encoding="utf-8",
    )


def _load_pending() -> dict:
    if not _PENDING.is_file():
        return {}
    try:
        return json.loads(_PENDING.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _whoami(creds) -> tuple[str, str]:
    """Название и ID канала, которому выдали разрешение."""
    try:
        from googleapiclient.discovery import build

        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
        if not items:
            return "", ""
        return items[0]["snippet"].get("title", ""), items[0].get("id", "")
    except Exception as e:  # сеть/квота — не повод терять уже полученный токен
        print(f"(не смог проверить канал: {e})", file=sys.stderr)
        return "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube OAuth setup for yt_prayer uploads")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--token", default="")
    parser.add_argument(
        "--code",
        default="",
        help="Код или полный URL http://localhost/?code=...",
    )
    parser.add_argument(
        "--expect-channel",
        default="",
        help=(
            "ID канала, который должен быть авторизован (UC...). Если Google "
            "вернул другой — токен не сохраняется."
        ),
    )
    args = parser.parse_args()

    secret = Path(args.client_secret) if args.client_secret else _DEFAULT_SECRET
    token = Path(args.token) if args.token else _DEFAULT_TOKEN
    if not secret.is_file():
        print(f"Нет client secret: {secret}", file=sys.stderr)
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
    except ImportError:
        print(
            "Нет google-auth-oauthlib:\n"
            "  .venv/bin/python -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2",
            file=sys.stderr,
        )
        return 1

    code = _extract_code(args.code)

    if code:
        flow = _make_flow(secret)
        pending = _load_pending()
        verifier = (pending.get("code_verifier") or "").strip()
        if verifier:
            flow.oauth2session.code_verifier = verifier
        try:
            flow.fetch_token(code=code)
        except Exception as e:
            print(f"Ошибка обмена кода: {e}", file=sys.stderr)
            print(
                "Код одноразовый и живёт ~10 мин. Запустите скрипт без --code заново.",
                file=sys.stderr,
            )
            return 1
        creds = flow.credentials
    else:
        flow = _make_flow(secret)
        auth_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
        verifier = getattr(flow.oauth2session, "code_verifier", None) or ""
        _save_pending(code_verifier=verifier or "", state=state or "")
        print("1) Откройте URL в браузере и разрешите доступ YouTube-каналу:")
        print(auth_url)
        print()
        # Подсказка повторяет ТЕ ЖЕ флаги, с которыми скрипт запущен. Без них
        # обмен кода сохранит токен в путь по умолчанию — то есть перезапишет
        # токен основного канала чужой авторизацией, и это заметно не сразу.
        same_flags = ""
        if args.token:
            same_flags += f" \\\n     --token {args.token}"
        if args.expect_channel:
            same_flags += f" \\\n     --expect-channel {args.expect_channel}"
        print(
            "2) После «Разрешить» браузер откроет http://localhost/?code=...\n"
            "   Страница может не загрузиться — скопируйте URL из адресной строки.\n"
            "3) На сервере выполните (вставьте свой URL):\n"
            f"   .venv/bin/python scripts/youtube_oauth_setup.py{same_flags} \\\n"
            "     --code 'http://localhost/?code=...'\n"
        )
        pasted = input("Или вставьте код/URL сюда сейчас: ").strip()
        code = _extract_code(pasted)
        if not code:
            print("Код не введён. Повторите с --code когда будет URL.", file=sys.stderr)
            return 1
        flow.fetch_token(code=code)
        creds = flow.credentials

    # Какой канал на самом деле авторизовали. При нескольких каналах в одном
    # аккаунте Google показывает выбор, и промахнуться легко — а промах значит,
    # что ролики одного языка молча уедут на канал другого.
    title, channel_id = _whoami(creds)
    if channel_id:
        print(f"Авторизован канал: {title} ({channel_id})")
    else:
        print("ВНИМАНИЕ: не удалось определить канал — проверьте вручную.")

    expect = (args.expect_channel or "").strip()
    if expect and channel_id and expect != channel_id:
        print(
            f"ОТМЕНА: ожидали канал {expect}, а разрешение выдано для "
            f"{channel_id} ({title}). Токен НЕ сохранён.\n"
            "Запустите снова и на экране Google выберите нужный канал.",
            file=sys.stderr,
        )
        return 1

    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    if _PENDING.is_file():
        _PENDING.unlink(missing_ok=True)
    print(f"OK: token сохранён в {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
