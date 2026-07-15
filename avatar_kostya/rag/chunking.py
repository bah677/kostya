"""Разбиение текста на чанки по токенам (tiktoken). Без I/O."""

from __future__ import annotations

import logging
from typing import List

import tiktoken

logger = logging.getLogger(__name__)


def get_encoder(encoding_name: str):
    try:
        return tiktoken.get_encoding(encoding_name)
    except KeyError:
        logger.warning("Encoding %s not found, using cl100k_base", encoding_name)
        return tiktoken.get_encoding("cl100k_base")


def chunk_text_by_tokens(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    encoding_name: str = "cl100k_base",
) -> List[str]:
    """
    Делит текст на чанки длиной ``chunk_size`` токенов с перекрытием ``overlap``.
    Пустая строка → [].
    """
    if not text or not str(text).strip():
        return []

    enc = get_encoder(encoding_name)
    tokens = enc.encode(text)
    if not tokens:
        return []

    size = max(1, chunk_size)
    ov = max(0, min(overlap, size - 1))
    step = max(1, size - ov)

    chunks: List[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        piece = tokens[start:end]
        decoded = enc.decode(piece)
        if decoded.strip():
            chunks.append(decoded)
        if end >= len(tokens):
            break
        start += step

    return chunks
