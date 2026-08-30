"""Выдержка из prod-логов бота по user_id (окно вокруг тикета)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_INTERESTING = re.compile(
    r"error|warning|failed|exception|permission denied|agent|prayer|support|"
    r"pending|incident|sigterm|polling|deepseek|empty|hard_fail|delivery",
    re.I,
)


def _default_log_paths() -> List[Path]:
    """Prod-путь + log/ рядом с проектом (dev)."""
    roots = [
        Path("/home/appuser/biblia/log"),
        Path(__file__).resolve().parents[2] / "log",
    ]
    names = ("biblia_bot.log", "biblia_bot_errors.log")
    out: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        for name in names:
            p = root / name
            key = str(p)
            if key not in seen and p.is_file():
                out.append(p)
                seen.add(key)
    return out


def _resolve_log_paths(configured: str) -> List[Path]:
    if configured.strip():
        paths: List[Path] = []
        for chunk in configured.split(","):
            p = Path(chunk.strip())
            if p.is_file():
                paths.append(p)
        if paths:
            return paths
    return _default_log_paths()


def _parse_log_time(line: str) -> Optional[datetime]:
    m = _LOG_TS.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_MSK)
    except ValueError:
        return None


def _anchor_msk(anchor_at: Optional[datetime]) -> datetime:
    if anchor_at is None:
        return datetime.now(_MSK)
    if anchor_at.tzinfo is None:
        return anchor_at.replace(tzinfo=_MSK)
    return anchor_at.astimezone(_MSK)


def _user_line_match(line: str, user_id: int) -> bool:
    uid = str(int(user_id))
    patterns = (
        rf"\buser_id={uid}\b",
        rf"\buser={uid}\b",
        rf"\buid={uid}\b",
        rf"\bот {uid}\b",
        rf" user {uid}\b",
        rf"Получено сообщение от {uid}:",
        rf" для user {uid}\b",
        rf"uid={uid}\b",
    )
    return any(re.search(p, line, re.I) for p in patterns)


def _line_score(line: str) -> int:
    score = 0
    if " - ERROR - " in line:
        score += 12
    elif " - WARNING - " in line:
        score += 6
    if _INTERESTING.search(line):
        score += 3
    return score


def _tail_lines(path: Path, *, max_bytes: int) -> List[str]:
    try:
        size = path.stat().st_size
        start = max(0, size - max_bytes)
        with path.open("rb") as f:
            if start:
                f.seek(start)
                f.readline()  # partial line
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()
    except OSError as e:
        logger.debug("support_log_excerpt read %s: %s", path, e)
        return []


def _collect_from_paths(
    paths: Sequence[Path],
    *,
    user_id: int,
    window_start: datetime,
    window_end: datetime,
    tail_bytes: int,
) -> List[tuple[datetime, int, str, str]]:
    """(ts, score, line, source_name)."""
    hits: List[tuple[datetime, int, str, str]] = []
    for path in paths:
        for line in _tail_lines(path, max_bytes=tail_bytes):
            if not _user_line_match(line, user_id):
                continue
            ts = _parse_log_time(line)
            if ts is None:
                continue
            if ts < window_start or ts > window_end:
                continue
            plain = line.strip()
            if len(plain) > 420:
                plain = plain[:417] + "…"
            hits.append((ts, _line_score(plain), plain, path.name))
    return hits


def fetch_user_log_excerpt_sync(
    user_id: int,
    *,
    anchor_at: Optional[datetime] = None,
    log_paths: Optional[Iterable[Path]] = None,
    window_before_min: int = 120,
    window_after_min: int = 5,
    tail_bytes: int = 3_000_000,
    max_lines: int = 20,
) -> List[str]:
    """Синхронное чтение хвоста логов. Вызывать через asyncio.to_thread."""
    paths = list(log_paths) if log_paths else _default_log_paths()
    if not paths:
        return []

    anchor = _anchor_msk(anchor_at)
    window_start = anchor - timedelta(minutes=window_before_min)
    window_end = anchor + timedelta(minutes=window_after_min)

    hits = _collect_from_paths(
        paths,
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
        tail_bytes=tail_bytes,
    )
    if not hits:
        return []

    hits.sort(key=lambda x: (-x[1], x[0]))
    top = hits[: max_lines * 2]
    top.sort(key=lambda x: x[0])

    out: List[str] = []
    seen: set[str] = set()
    for ts, _score, line, src in top:
        if line in seen:
            continue
        seen.add(line)
        out.append(f"[{ts.strftime('%H:%M:%S')} {src}] {line}")
        if len(out) >= max_lines:
            break
    return out


async def fetch_user_log_excerpt(
    user_id: int,
    *,
    anchor_at: Optional[datetime] = None,
    configured_paths: str = "",
    window_before_min: int = 120,
    window_after_min: int = 5,
    tail_bytes: int = 3_000_000,
    max_lines: int = 20,
) -> List[str]:
    import asyncio

    paths = _resolve_log_paths(configured_paths)
    return await asyncio.to_thread(
        fetch_user_log_excerpt_sync,
        user_id,
        anchor_at=anchor_at,
        log_paths=paths,
        window_before_min=window_before_min,
        window_after_min=window_after_min,
        tail_bytes=tail_bytes,
        max_lines=max_lines,
    )
