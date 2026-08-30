"""Дешёвый LLM-план: смысловые блоки молитвы → якоря + индексы стока."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

_SYSTEM = """Ты монтажёр духовного ролика. Разбей молитву на 6–8 смысловых блоков.

Для КАЖДОГО блока:
- label: 3–6 слов о смысле (RU);
- visual: короткий EN-промпт кадра (no text, no faces close-up) — атмосфера блока;
- is_anchor: true для смысловых ПИКОВ (просьба, боль, надежда, «аминь»), false для связок;
- scene_index: целое 0..pool_size-1 для запасного стока.

Правила:
- ровно 6–8 блоков;
- is_anchor=true у 4–8 пиков (не у всех подряд связок);
- scene_index не ставь одинаковый подряд;
- повторы scene_index разноси максимально далеко.

Верни ТОЛЬКО JSON:
{"blocks":[{"label":"...","visual":"...","is_anchor":true,"scene_index":0}, ...]}
"""


@dataclass(frozen=True)
class SemanticBlock:
    label: str
    visual: str
    is_anchor: bool
    scene_index: int


def _parse_blocks(raw: str, *, pool_size: int) -> List[SemanticBlock]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return []
    out: List[SemanticBlock] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        label = str(b.get("label") or "").strip()[:80]
        visual = str(b.get("visual") or "").strip()[:240]
        if not label and not visual:
            continue
        try:
            idx = int(b.get("scene_index", len(out)))
        except (TypeError, ValueError):
            idx = len(out)
        if pool_size > 0:
            idx = idx % pool_size
        anchor_raw = b.get("is_anchor")
        if isinstance(anchor_raw, bool):
            is_anchor = anchor_raw
        else:
            is_anchor = str(anchor_raw or "true").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        out.append(
            SemanticBlock(
                label=label or f"block_{len(out)+1}",
                visual=visual
                or "cinematic peaceful Christian prayer atmosphere, soft golden light",
                is_anchor=is_anchor,
                scene_index=idx,
            )
        )
    return out


def _spread_fallback(pool_size: int, n_slots: int) -> List[int]:
    if pool_size <= 0:
        return [0] * max(1, n_slots)
    step = max(1, pool_size // 2) if pool_size > 2 else 1
    out: List[int] = []
    cur = 0
    for _ in range(max(1, n_slots)):
        out.append(cur % pool_size)
        cur += step
        if pool_size > 1 and len(out) >= 2 and out[-1] == out[-2]:
            cur += 1
            out[-1] = cur % pool_size
    return out


def _default_blocks(n: int = 7) -> List[SemanticBlock]:
    presets = [
        ("обращение", "dawn light through church window, empty pews, soft dust", True),
        ("боль", "rain on window glass, dim room, lonely chair", True),
        ("просьба", "hands open upward in soft golden light, no face", True),
        ("писание", "open bible pages, warm candle light, shallow depth", True),
        ("надежда", "sunrise over calm sea, pastel sky, peaceful", True),
        ("благодарность", "wildflowers in soft breeze, golden hour field", True),
        ("аминь", "cross silhouette against warm sunset sky, reverent", True),
        ("тишина", "misty forest path, soft morning fog, quiet", False),
    ]
    out: List[SemanticBlock] = []
    for i in range(max(6, min(8, n))):
        label, visual, anc = presets[i % len(presets)]
        out.append(
            SemanticBlock(
                label=label,
                visual=visual,
                is_anchor=anc,
                scene_index=i,
            )
        )
    return out


async def plan_semantic_blocks(
    prayer_text: str,
    *,
    pool_size: int = 12,
    complete_fn=None,
) -> List[SemanticBlock]:
    """6–8 смысловых блоков с visual для AI-якорей."""
    pool_size = max(1, int(pool_size))
    if complete_fn is None or not (prayer_text or "").strip():
        return _default_blocks()

    body = " ".join((prayer_text or "").split())
    if len(body) > 3500:
        body = body[:3500] + "…"
    user = f"pool_size={pool_size}\n\nМолитва:\n{body}"
    try:
        raw = await complete_fn(_SYSTEM, user)
        blocks = _parse_blocks(raw or "", pool_size=pool_size)
    except Exception as e:
        logger.warning("semantic blocks LLM failed: %s", e)
        blocks = []

    if len(blocks) < 4:
        logger.info("semantic blocks fallback defaults")
        return _default_blocks()

    # если якорей слишком мало — пометить пики по позиции
    anchors = sum(1 for b in blocks if b.is_anchor)
    if anchors < 4:
        fixed: List[SemanticBlock] = []
        for i, b in enumerate(blocks):
            force = i in {0, len(blocks) // 3, (2 * len(blocks)) // 3, len(blocks) - 1}
            fixed.append(
                SemanticBlock(
                    label=b.label,
                    visual=b.visual,
                    is_anchor=b.is_anchor or force,
                    scene_index=b.scene_index,
                )
            )
        blocks = fixed

    logger.info(
        "semantic blocks ok n=%s anchors=%s",
        len(blocks),
        sum(1 for b in blocks if b.is_anchor),
    )
    return blocks


def expand_blocks_to_slots(
    blocks: Sequence[SemanticBlock],
    *,
    n_slots: int,
    pool_size: int,
) -> List[int]:
    """Растянуть scene_index блоков на n_slots (сток-фолбэк)."""
    if not blocks:
        return _spread_fallback(pool_size, n_slots)
    out: List[int] = []
    for i in range(max(1, n_slots)):
        bi = int(i * len(blocks) / n_slots)
        bi = min(len(blocks) - 1, bi)
        out.append(blocks[bi].scene_index % max(1, pool_size))
    for i in range(1, len(out)):
        if out[i] == out[i - 1] and pool_size > 1:
            out[i] = (out[i] + 1 + (i % (pool_size - 1))) % pool_size
    return out


def slot_block_indices(blocks: Sequence[SemanticBlock], *, n_slots: int) -> List[int]:
    """Для каждого слота таймлайна — индекс смыслового блока."""
    if not blocks:
        return [0] * max(1, n_slots)
    out: List[int] = []
    for i in range(max(1, n_slots)):
        bi = int(i * len(blocks) / n_slots)
        out.append(min(len(blocks) - 1, bi))
    return out


async def plan_scene_indices(
    prayer_text: str,
    *,
    pool_size: int,
    n_slots: int,
    complete_fn=None,
) -> List[int]:
    """Совместимость: только индексы стока на слоты."""
    blocks = await plan_semantic_blocks(
        prayer_text, pool_size=pool_size, complete_fn=complete_fn
    )
    return expand_blocks_to_slots(blocks, n_slots=n_slots, pool_size=pool_size)
