"""Состояние токенов YouTube и перевыпуск прямо из контент-завода.

Токены живут около недели и умирают молча: за месяц оба канала дважды
останавливались на сутки, и каждый раз перевыпуск шёл через разработчика —
сгенерировать ссылку, открыть в браузере, прислать обратно URL с кодом.

Здесь то же самое, но в интерфейсе: видно, жив ли ключ каждого канала и
когда обновлялся, кнопка даёт ссылку, обратно вставляется адрес из адресной
строки. Канал сверяется с ожидаемым — промахнуться и выдать доступ чужому
каналу нельзя.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from youtube_prayer.langs import known_langs, normalize_lang, profile

logger = logging.getLogger(__name__)

# Те же права, что у консольного скрипта: загрузка, управление каналом и
# комментарии. Меньше нельзя — пайплайн ими пользуется.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_PENDING_NAME = "web_oauth_pending.json"


def _uploader():
    from youtube_prayer import youtube_uploader as u

    return u


def _pending_path() -> Path:
    return _uploader().token_path().parent / _PENDING_NAME


def _read_pending() -> Dict[str, Any]:
    p = _pending_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_pending(data: Dict[str, Any]) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _human_age(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    delta = datetime.now(timezone.utc) - dt
    h = delta.total_seconds() / 3600
    if h < 1:
        return "только что"
    if h < 48:
        return f"{int(h)} ч назад"
    return f"{int(h // 24)} дн назад"


def key_status(lang: str) -> Dict[str, Any]:
    """Жив ли токен канала. Проверяется реальным обновлением, не чтением файла."""
    u = _uploader()
    code = normalize_lang(lang)
    prof = profile(code)
    tok = u.token_path(code)
    out: Dict[str, Any] = {
        "lang": code,
        "label": prof.label,
        "token_file": tok.name,
        "expected_channel_id": prof.youtube_channel_id,
        "exists": tok.is_file(),
        "alive": False,
        "channel_title": "",
        "channel_id": "",
        "subscribers": None,
        "videos": None,
        "views": None,
        "refreshed_at": "",
        "refreshed_human": "",
        "error": "",
    }
    if not tok.is_file():
        out["error"] = "токен не выпускался"
        return out
    try:
        ts = datetime.fromtimestamp(tok.stat().st_mtime, tz=timezone.utc)
        out["refreshed_at"] = ts.isoformat(timespec="seconds")
        out["refreshed_human"] = _human_age(ts)
    except Exception:
        pass
    try:
        yt = u._youtube_service(code)
        ch = yt.channels().list(part="snippet,statistics", mine=True).execute()["items"][0]
        st = ch.get("statistics") or {}
        out.update(
            alive=True,
            channel_title=ch["snippet"].get("title", ""),
            channel_id=ch.get("id", ""),
            subscribers=int(st.get("subscriberCount") or 0),
            videos=int(st.get("videoCount") or 0),
            views=int(st.get("viewCount") or 0),
        )
        if prof.youtube_channel_id and ch.get("id") != prof.youtube_channel_id:
            out["error"] = (
                f"токен выдан другому каналу: {ch['snippet'].get('title')} "
                f"({ch.get('id')})"
            )
    except Exception as e:
        msg = str(e)
        if "invalid_grant" in msg or "expired or revoked" in msg:
            out["error"] = "токен отозван или истёк — нужен перевыпуск"
        else:
            out["error"] = f"{type(e).__name__}: {msg[:160]}"
    return out


def all_key_statuses() -> List[Dict[str, Any]]:
    """Только языки со своим каналом.

    Проверять наличие файла токена тут нельзя: token_path для языка без
    своего токена падает на общий, и английский показывался как ещё одна
    карточка русского канала.
    """
    return [key_status(code) for code in known_langs() if profile(code).youtube_channel_id]


def _make_flow(lang: str):
    from google_auth_oauthlib.flow import InstalledAppFlow

    u = _uploader()
    secret = u.client_secrets_path(normalize_lang(lang))
    if not secret.is_file():
        raise FileNotFoundError(f"нет client secret: {secret}")
    data = json.loads(secret.read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or {}
    uris = block.get("redirect_uris") or ["http://localhost"]
    redirect = next(
        (x for x in ("http://localhost", "http://127.0.0.1") if x in uris), uris[0]
    )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(secret), SCOPES, autogenerate_code_verifier=False
    )
    flow.redirect_uri = redirect
    return flow


def build_auth_url(lang: str) -> Dict[str, Any]:
    code = normalize_lang(lang)
    flow = _make_flow(code)
    url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    pending = _read_pending()
    pending[code] = {
        "state": state or "",
        "code_verifier": getattr(flow.oauth2session, "code_verifier", None) or "",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_pending(pending)
    prof = profile(code)
    return {
        "lang": code,
        "label": prof.label,
        "url": url,
        "expected_channel_id": prof.youtube_channel_id,
        "hint": (
            "Откройте ссылку и выберите нужный канал. После «Разрешить» "
            "браузер уйдёт на localhost и покажет ошибку — это нормально. "
            "Скопируйте адрес из адресной строки целиком и вставьте сюда."
        ),
    }


def _extract_code(raw: str) -> str:
    import re
    from urllib.parse import parse_qs, urlparse

    text = (raw or "").strip()
    if not text:
        return ""
    if "code=" in text:
        qs = parse_qs(urlparse(text).query)
        got = (qs.get("code") or [""])[0]
        if got:
            return got
        m = re.search(r"[?&]code=([^&]+)", text)
        if m:
            return m.group(1)
    return text


def exchange_code(lang: str, raw_code: str) -> Dict[str, Any]:
    """Меняет код на токен. Чужой канал не сохраняем."""
    u = _uploader()
    code = normalize_lang(lang)
    prof = profile(code)
    auth_code = _extract_code(raw_code)
    if not auth_code:
        raise ValueError("не нашёл код в присланном адресе")

    flow = _make_flow(code)
    verifier = (_read_pending().get(code) or {}).get("code_verifier") or ""
    if verifier:
        flow.oauth2session.code_verifier = verifier
    flow.fetch_token(code=auth_code)
    creds = flow.credentials

    # Какой канал на самом деле авторизовали.
    from googleapiclient.discovery import build

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        raise RuntimeError("не удалось определить канал — токен не сохранён")
    got_id = items[0].get("id", "")
    got_title = items[0]["snippet"].get("title", "")
    if prof.youtube_channel_id and got_id != prof.youtube_channel_id:
        raise ValueError(
            f"разрешение выдано каналу «{got_title}», а ожидался "
            f"{prof.label}. Токен не сохранён — откройте ссылку снова и "
            "выберите нужный канал."
        )

    tok = u.token_path(code)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json(), encoding="utf-8")
    pending = _read_pending()
    pending.pop(code, None)
    _write_pending(pending)
    logger.info("web oauth: токен %s перевыпущен для %s (%s)", code, got_title, got_id)
    return {"ok": True, "lang": code, "channel_title": got_title, "channel_id": got_id}
