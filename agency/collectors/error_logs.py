"""Сбор error-логов экосистемы за сутки (все ротации/архивы)."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

# Строка лога: 2026-07-25 08:02:35,628 - ...
_LINE_TS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}(?:,\d+)?\s+"
)
_DATE_IN_NAME = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_NOISE_NUM = re.compile(r"\b\d{5,}\b")
_NOISE_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
_NOISE_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.I)


@dataclass(frozen=True)
class ProjectLogSpec:
    project: str
    roots: Tuple[Path, ...]
    # glob относительно root; текущий + ротированные
    patterns: Tuple[str, ...] = (
        "*error*.log",
        "*errors*.log",
        "arc/*error*.log",
        "arc/*errors*.log",
    )


DEFAULT_SPECS: Tuple[ProjectLogSpec, ...] = (
    ProjectLogSpec(
        project="club",
        roots=(Path("/home/appuser/club/log"),),
        patterns=("bot-errors.log", "arc/bot-errors*.log"),
    ),
    ProjectLogSpec(
        project="biblia",
        roots=(Path("/home/appuser/biblia/log"),),
        patterns=("biblia_bot_errors.log", "biblia_bot_errors-*.log"),
    ),
    ProjectLogSpec(
        project="avatar_kostya",
        roots=(Path("/home/appuser/dev/kostya/avatar_kostya/log"),),
        patterns=("bot-errors.log", "arc/bot-errors*.log"),
    ),
)


@dataclass
class ErrorCluster:
    signature: str
    count: int
    sample: str
    files: List[str] = field(default_factory=list)


@dataclass
class ProjectErrorDigest:
    project: str
    day: date
    files_scanned: List[str]
    files_with_hits: List[str]
    total_events: int
    clusters: List[ErrorCluster]


def _parse_yyyymmdd(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _name_dates(path: Path) -> List[date]:
    out: List[date] = []
    for m in _DATE_IN_NAME.finditer(path.name):
        d = _parse_yyyymmdd(m.group(1))
        if d:
            out.append(d)
    return out


def _file_may_contain_day(path: Path, day: date) -> bool:
    """Эвристика: не пропускаем файл, если он мог писаться в target day."""
    names = _name_dates(path)
    if names and day in names:
        return True
    # текущий активный лог без даты в имени — всегда смотрим
    if not names and path.suffix == ".log":
        return True
    # ротация «на следующий день» может содержать хвост вчерашнего
    if names and any(abs((n - day).days) <= 1 for n in names):
        return True
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_MSK)
    except OSError:
        return False
    start = datetime.combine(day, datetime.min.time(), tzinfo=_MSK)
    end = start + timedelta(days=1, hours=6)
    # файл трогали в окне суток (+ запас на ротацию)
    return start - timedelta(hours=6) <= mtime <= end


def discover_files(spec: ProjectLogSpec, day: date) -> List[Path]:
    found: List[Path] = []
    seen = set()
    for root in spec.roots:
        if not root.is_dir():
            logger.warning("log root missing for %s: %s", spec.project, root)
            continue
        for pattern in spec.patterns:
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                if not _file_may_contain_day(path, day):
                    continue
                seen.add(key)
                found.append(path)
    return found


def normalize_signature(text: str) -> str:
    t = text.strip()
    t = _LINE_TS.sub("", t, count=1)
    t = _NOISE_UUID.sub("<uuid>", t)
    t = _NOISE_HEX.sub("<hex>", t)
    t = _NOISE_NUM.sub("<n>", t)
    t = re.sub(r"\s+", " ", t)
    return t[:400]


def _read_day_events(path: Path, day: date, max_bytes: int = 8_000_000) -> List[str]:
    """События ERROR за day: заголовок + продолжение traceback до следующей ts-строки."""
    day_s = day.isoformat()
    events: List[str] = []
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
                f.readline()  # skip partial
            cur: List[str] = []
            cur_is_day = False
            for line in f:
                m = _LINE_TS.match(line)
                if m:
                    if cur and cur_is_day:
                        events.append("".join(cur).rstrip()[:2500])
                    cur = [line]
                    cur_is_day = m.group("ts") == day_s
                else:
                    if cur:
                        cur.append(line)
            if cur and cur_is_day:
                events.append("".join(cur).rstrip()[:2500])
    except OSError as e:
        logger.warning("read log failed %s: %s", path, e)
    return events


def cluster_events(
    events: Sequence[str], *, top_n: int = 12
) -> List[ErrorCluster]:
    buckets: Dict[str, List[str]] = defaultdict(list)
    for ev in events:
        # берём первую строку как основу сигнатуры (+ кусок traceback)
        first = ev.splitlines()[0] if ev else ""
        # добавим тип исключения из хвоста если есть
        sig_src = first
        for ln in reversed(ev.splitlines()):
            if re.match(r"^\w+(Error|Exception|Timeout|Failed)\b", ln.strip()):
                sig_src = f"{first} || {ln.strip()}"
                break
        sig = normalize_signature(sig_src)
        if not sig:
            continue
        buckets[sig].append(ev)
    ranked = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out: List[ErrorCluster] = []
    for sig, samples in ranked[:top_n]:
        out.append(
            ErrorCluster(
                signature=sig,
                count=len(samples),
                sample=samples[0][:1200],
            )
        )
    return out


def collect_project_digest(
    spec: ProjectLogSpec,
    day: date,
    *,
    top_n: int = 12,
) -> ProjectErrorDigest:
    files = discover_files(spec, day)
    all_events: List[str] = []
    files_with_hits: List[str] = []
    file_hits: Counter = Counter()
    for path in files:
        events = _read_day_events(path, day)
        if events:
            files_with_hits.append(str(path))
            file_hits[str(path)] = len(events)
            all_events.extend(events)
    clusters = cluster_events(all_events, top_n=top_n)
    # annotate which files contributed (best-effort: all hit files)
    for c in clusters:
        c.files = list(files_with_hits)[:8]
    return ProjectErrorDigest(
        project=spec.project,
        day=day,
        files_scanned=[str(p) for p in files],
        files_with_hits=files_with_hits,
        total_events=len(all_events),
        clusters=clusters,
    )


def collect_all_digests(
    day: date,
    specs: Optional[Sequence[ProjectLogSpec]] = None,
    *,
    top_n: int = 12,
) -> List[ProjectErrorDigest]:
    specs = list(specs or DEFAULT_SPECS)
    return [collect_project_digest(s, day, top_n=top_n) for s in specs]


def format_digest_blob(digests: Sequence[ProjectErrorDigest], *, max_chars: int = 28000) -> str:
    parts: List[str] = []
    for d in digests:
        parts.append(f"=== PROJECT {d.project} day={d.day} ===")
        parts.append(
            f"files_scanned={len(d.files_scanned)} "
            f"files_with_hits={len(d.files_with_hits)} "
            f"total_events={d.total_events}"
        )
        if d.files_with_hits:
            parts.append("hit_files:")
            for f in d.files_with_hits[:12]:
                parts.append(f"  - {f}")
        if not d.clusters:
            parts.append("(no ERROR events for this day)")
            parts.append("")
            continue
        for i, c in enumerate(d.clusters, 1):
            parts.append(f"--- cluster #{i} count={c.count} ---")
            parts.append(f"signature: {c.signature}")
            parts.append("sample:")
            parts.append(c.sample)
            parts.append("")
    blob = "\n".join(parts)
    if len(blob) > max_chars:
        return blob[: max_chars - 80] + "\n\n…[truncated for LLM budget]…"
    return blob


def specs_from_config_paths(
    mapping: Dict[str, Sequence[str]],
) -> List[ProjectLogSpec]:
    """mapping: project -> list of root dirs (строки)."""
    out: List[ProjectLogSpec] = []
    defaults = {s.project: s for s in DEFAULT_SPECS}
    for project, roots in mapping.items():
        base = defaults.get(project)
        patterns = base.patterns if base else ("*error*.log", "arc/*error*.log")
        out.append(
            ProjectLogSpec(
                project=project,
                roots=tuple(Path(r) for r in roots if r),
                patterns=patterns,
            )
        )
    return out
