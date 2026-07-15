"""Разбор аргументов команды /report для админов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ReportRunOptions:
    """Что включить в отчёт по команде /report."""

    include_v2: bool = True
    include_llm: bool = True
    # None — взять из config.REPORT_LEGACY_ENABLED
    include_legacy: Optional[bool] = None
    #: Только компактный блок «Библия → Клуб» (без legacy/v2/DeepSeek).
    biblia_club_only: bool = False


def parse_report_command_args(args: Optional[str]) -> ReportRunOptions:
    opts = ReportRunOptions()
    raw = (args or "").strip().lower()
    if not raw:
        return opts
    for tok in raw.replace(",", " ").split():
        t = tok.strip()
        if not t:
            continue
        if t in ("--no-v2", "no-v2", "no_v2", "nov2", "без-v2"):
            opts.include_v2 = False
        elif t in (
            "--no-llm",
            "no-llm",
            "no_llm",
            "nollm",
            "metrics",
            "цифры",
            "numbers",
            "без-llm",
            "без-deepseek",
        ):
            opts.include_llm = False
        elif t in ("legacy", "legacy-only", "--legacy", "только-legacy"):
            opts.include_v2 = False
            opts.include_legacy = True
        elif t in ("v2", "v2-only", "--v2", "только-v2"):
            opts.include_legacy = False
        elif t in (
            "biblia",
            "biblia_club",
            "biblia-club",
            "библия",
            "--biblia",
        ):
            opts.biblia_club_only = True
            opts.include_v2 = False
            opts.include_legacy = False
            opts.include_llm = False
    return opts


def format_report_options_hint() -> str:
    return (
        "<b>/report</b> — полный отчёт\n"
        "• <code>/report biblia</code> — только блок «Библия → Клуб»\n"
        "• <code>/report metrics</code> — v2 без DeepSeek (только цифры)\n"
        "• <code>/report --no-v2</code> — только legacy\n"
        "• <code>/report v2 metrics</code> — только v2, без LLM\n"
        "• <code>/report legacy</code> — только legacy"
    )
