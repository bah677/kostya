"""Ежедневный отчёт расходов LLM/TTS/images по задачам.

Группировка request_kind → продуктовые блоки, оценка $ по тарифам моделей.
Отправка в админ-группу клуба (топик «статистика»), бот — CLUB_BOT_TOKEN.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")

# ── категории задач (порядок = порядок в отчёте) ─────────────────────────────

@dataclass(frozen=True)
class TaskBucket:
    key: str
    title: str
    emoji: str
    kinds: frozenset[str]


TASK_BUCKETS: Tuple[TaskBucket, ...] = (
    TaskBucket(
        "dm",
        "Личка с ботом",
        "💬",
        frozenset(
            {
                "chat_completion",
                "content_writer",
                "deepseek_task_messages",
                "telegram_html_format_auxiliary",
                "vision_chat_completion",
                "whisper_transcription",
            }
        ),
    ),
    TaskBucket(
        "youtube",
        "YouTube: молитвы и Shorts",
        "🎬",
        frozenset(
            {
                "yt_prayer_compose",
                "cover_image",
                "broll_image",
                "tts",
                "tts_with_timestamps",
                "channel_stats_analyze",
            }
        ),
    ),
    TaskBucket(
        "reels",
        "Reels: сценарии",
        "📱",
        frozenset({"reels_openai", "reels_rubric"}),
    ),
    TaskBucket(
        "telemost",
        "Эфиры: нарезка и описания",
        "🎙",
        frozenset(
            {
                "audio_moments_extract",
                "audio_moments_editor",
                "audio_captions",
                "full_voice_caption",
                "caption_revision",
                "telemost_classify",
                "transcript_extract",
                "shorts_moments_extract",
            }
        ),
    ),
    TaskBucket(
        "rag",
        "RAG / индекс / метаданные",
        "📚",
        frozenset(
            {
                "rag_tags",
                "rag_retrieval_plan",
                "yadisk_metadata",
                "course_mining",
                "course_planner",
                "embedding",
            }
        ),
    ),
)

_KIND_TO_BUCKET: Dict[str, TaskBucket] = {}
for _b in TASK_BUCKETS:
    for _k in _b.kinds:
        _KIND_TO_BUCKET[_k] = _b


def _bucket_for(kind: Optional[str]) -> TaskBucket:
    k = (kind or "").strip()
    if k in _KIND_TO_BUCKET:
        return _KIND_TO_BUCKET[k]
    # эвристики по префиксу
    if k.startswith("yt_") or k.startswith("tts"):
        return _KIND_TO_BUCKET["yt_prayer_compose"]
    if k.startswith("reels"):
        return _KIND_TO_BUCKET["reels_openai"]
    if k.startswith("audio_") or k.startswith("telemost") or k.startswith("caption"):
        return next(b for b in TASK_BUCKETS if b.key == "telemost")
    if k.startswith("rag_") or k.startswith("course_") or k.startswith("yadisk"):
        return next(b for b in TASK_BUCKETS if b.key == "rag")
    return TaskBucket("other", "Прочее", "🧩", frozenset())


# ── тарифы (USD): оценка, править в одном месте ──────────────────────────────
# token rates: (input_per_1M, output_per_1M)
# elevenlabs: USD per 1000 characters (prompt_tokens = chars)
# image: USD per image (when tokens are 0)

_TOKEN_RATES: Dict[str, Tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "text-embedding-3-small": (0.02, 0.0),
    "whisper-1": (0.0, 0.0),  # отдельно: см. per_request
    # DeepSeek — средний между peak/off-peak flash
    "deepseek-v4-flash": (0.30, 0.90),
    "deepseek-chat": (0.30, 0.90),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v4-pro": (0.99, 2.97),
}

_IMAGE_PER_CALL: Dict[str, float] = {
    "gpt-image-1": 0.04,
    "dall-e-3": 0.08,
    "dall-e-2": 0.02,
}

_ELEVEN_PER_1K_CHARS: Dict[str, float] = {
    "eleven_flash_v2_5": 0.06,
    "eleven_multilingual_v2": 0.18,
    "eleven_turbo_v2_5": 0.06,
}
_ELEVEN_DEFAULT_PER_1K = 0.10

# Whisper: нет токенов в логе — фиксированная оценка за запрос (~1 мин)
_WHISPER_USD_PER_REQUEST = 0.006


def _norm_model(model: str) -> str:
    return (model or "").strip().lower()


def estimate_row_usd(
    *,
    provider: str,
    model: str,
    request_kind: str,
    prompt_tokens: int,
    completion_tokens: int,
    request_count: int,
) -> Tuple[float, str]:
    """(usd, note). note — короткая пометка к строке."""
    prov = (provider or "").strip().lower()
    mid = _norm_model(model)
    kind = (request_kind or "").strip()
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    n = max(1, int(request_count or 0))

    if prov == "elevenlabs" or kind.startswith("tts"):
        rate = _ELEVEN_PER_1K_CHARS.get(mid, _ELEVEN_DEFAULT_PER_1K)
        usd = (pt / 1000.0) * rate
        return usd, f"{pt} симв"

    if kind in {"cover_image", "broll_image", "image_generation"} or mid.startswith(
        ("gpt-image", "dall-e")
    ):
        per = _IMAGE_PER_CALL.get(mid, 0.04)
        return per * n, f"{n}×картинка"

    if mid == "whisper-1" or kind == "whisper_transcription":
        return _WHISPER_USD_PER_REQUEST * n, f"{n}×asr"

    rates = _TOKEN_RATES.get(mid)
    if rates is None:
        # эвристика по семейству
        if "mini" in mid:
            rates = (0.15, 0.60)
        elif "flash" in mid or "deepseek" in mid:
            rates = (0.30, 0.90)
        elif "gpt-4o" in mid or "gpt-4.1" in mid:
            rates = (2.50, 10.0)
        else:
            rates = (1.0, 3.0)
    pin, pout = rates
    usd = (pt / 1_000_000.0) * pin + (ct / 1_000_000.0) * pout
    return usd, f"{_fmt_tokens(pt + ct)} ток"


def _fmt_tokens(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_usd(x: float) -> str:
    if x < 0.005:
        return "&lt;$0.01"
    if x < 1:
        return f"${x:.2f}"
    if x < 100:
        return f"${x:.2f}"
    return f"${x:.0f}"


def _fmt_rub(usd: float, usd_rub: Optional[float]) -> str:
    if not usd_rub or usd_rub <= 0:
        return ""
    rub = usd * usd_rub
    if rub < 1:
        return "&lt;1 ₽"
    if rub < 100:
        return f"{rub:.0f} ₽"
    return f"{rub:,.0f} ₽".replace(",", " ")


@dataclass
class CostLine:
    provider: str
    model: str
    request_kind: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    usd: float
    note: str
    bucket: TaskBucket


@dataclass
class DayCostReport:
    day: date
    lines: List[CostLine] = field(default_factory=list)
    usd_rub: Optional[float] = None

    @property
    def total_usd(self) -> float:
        return sum(x.usd for x in self.lines)

    @property
    def total_requests(self) -> int:
        return sum(x.requests for x in self.lines)


async def fetch_day_usage_rows(storage: Any, day: date) -> List[Dict[str, Any]]:
    """Агрегат за календарный день МСК (created_date = UTC date в схеме —
    для отчёта берём окно [day 00:00 MSK, next 00:00 MSK))."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=_MSK)
    end = start + timedelta(days=1)
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                COALESCE(provider, 'unknown') AS provider,
                COALESCE(request_kind, 'unknown') AS request_kind,
                COALESCE(model, 'unknown') AS model,
                COUNT(*)::int AS request_count,
                COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens
            FROM token_usage
            WHERE created_at >= $1 AND created_at < $2
            GROUP BY 1, 2, 3
            ORDER BY request_count DESC
            """,
            start,
            end,
        )
    return [dict(r) for r in rows]


async def build_day_report(
    storage: Any,
    day: date,
    *,
    usd_rub: Optional[float] = None,
) -> DayCostReport:
    rows = await fetch_day_usage_rows(storage, day)
    lines: List[CostLine] = []
    for r in rows:
        bucket = _bucket_for(str(r.get("request_kind") or ""))
        usd, note = estimate_row_usd(
            provider=str(r.get("provider") or ""),
            model=str(r.get("model") or ""),
            request_kind=str(r.get("request_kind") or ""),
            prompt_tokens=int(r.get("prompt_tokens") or 0),
            completion_tokens=int(r.get("completion_tokens") or 0),
            request_count=int(r.get("request_count") or 0),
        )
        lines.append(
            CostLine(
                provider=str(r.get("provider") or ""),
                model=str(r.get("model") or ""),
                request_kind=str(r.get("request_kind") or ""),
                requests=int(r.get("request_count") or 0),
                prompt_tokens=int(r.get("prompt_tokens") or 0),
                completion_tokens=int(r.get("completion_tokens") or 0),
                usd=usd,
                note=note,
                bucket=bucket,
            )
        )
    return DayCostReport(day=day, lines=lines, usd_rub=usd_rub)


def format_report_html(report: DayCostReport) -> str:
    """Компактный HTML для Telegram (parse_mode=HTML)."""
    day_s = report.day.strftime("%d.%m.%Y")
    total = report.total_usd
    rub = _fmt_rub(total, report.usd_rub)
    head = (
        f"💸 <b>Расходы нейросетей</b> · {day_s}\n"
        f"<b>Итого ≈ {_fmt_usd(total)}</b>"
        + (f" <i>({rub})</i>" if rub else "")
        + f" · {report.total_requests} вызовов"
    )
    if not report.lines:
        return head + "\n\nЗа день записей в token_usage нет."

    # bucket → lines sorted by usd desc
    by_bucket: Dict[str, List[CostLine]] = {}
    order: List[TaskBucket] = []
    seen = set()
    for b in TASK_BUCKETS:
        order.append(b)
        seen.add(b.key)
    # other last
    other = TaskBucket("other", "Прочее", "🧩", frozenset())
    if "other" not in seen:
        order.append(other)

    for line in report.lines:
        by_bucket.setdefault(line.bucket.key, []).append(line)

    blocks: List[str] = [head, ""]
    for bucket in order:
        items = by_bucket.get(bucket.key) or []
        if not items:
            continue
        items.sort(key=lambda x: -x.usd)
        b_usd = sum(x.usd for x in items)
        b_rub = _fmt_rub(b_usd, report.usd_rub)
        share = (100.0 * b_usd / total) if total > 0 else 0.0
        blocks.append(
            f"{bucket.emoji} <b>{bucket.title}</b> — {_fmt_usd(b_usd)}"
            + (f" · {b_rub}" if b_rub else "")
            + (f" · {share:.0f}%" if total > 0 else "")
        )
        # топ моделей в блоке (схлопнуть одинаковые model)
        model_agg: Dict[str, Tuple[float, int, str]] = {}
        for it in items:
            key = f"{it.provider}/{it.model}"
            prev = model_agg.get(key)
            if prev:
                model_agg[key] = (prev[0] + it.usd, prev[1] + it.requests, it.note)
            else:
                model_agg[key] = (it.usd, it.requests, it.note)
        ranked = sorted(model_agg.items(), key=lambda kv: -kv[1][0])[:4]
        for key, (u, nreq, note) in ranked:
            short_model = key.split("/", 1)[-1]
            if len(short_model) > 28:
                short_model = short_model[:27] + "…"
            blocks.append(
                f"  · <code>{short_model}</code> {_fmt_usd(u)}"
                f" · {nreq}× · {note}"
            )
        blocks.append("")

    blocks.append(
        "<i>Оценка по тарифам API (peak/off-peak усреднены). "
        "ElevenLabs — по символам; картинки — за штуку.</i>"
    )
    text = "\n".join(blocks).rstrip()
    # TG limit 4096
    if len(text) > 4000:
        text = text[:3990] + "…"
    return text


async def resolve_usd_rub(converter: Any) -> Optional[float]:
    if converter is None:
        return None
    try:
        from datetime import date as _date

        if hasattr(converter, "get_rate_to_rub"):
            r = await converter.get_rate_to_rub("USD", _date.today())
            return float(r) if r else None
    except Exception as e:
        logger.debug("usd_rub rate: %s", e)
    return None
