"""Фабрика embedding function для Chroma (OpenAI). Отдельно от остального кода."""

from __future__ import annotations

import logging

from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

# text-embedding-3-small / ada-002: лимит API 8192 токена на input.
_EMBED_MAX_TOKENS = 8192
_EMBED_SAFE_TOKENS = 7500  # запас под служебные токены / расхождение кодировок
_FALLBACK_CHAR_LIMIT = 24_000  # ~если tiktoken недоступен


def truncate_for_embedding(
    text: str,
    *,
    max_tokens: int = _EMBED_SAFE_TOKENS,
    encoding_name: str = "cl100k_base",
) -> str:
    """Обрезает query/document перед OpenAI embeddings, чтобы не ловить 400 (8192)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    limit = max(256, min(int(max_tokens), _EMBED_MAX_TOKENS - 64))
    try:
        import tiktoken

        enc = tiktoken.get_encoding(encoding_name)
        tokens = enc.encode(raw)
        if len(tokens) <= limit:
            return raw
        out = enc.decode(tokens[:limit]).strip()
        logger.info(
            "embed query truncated tokens=%s→%s chars=%s→%s",
            len(tokens),
            limit,
            len(raw),
            len(out),
        )
        return out
    except Exception as e:
        logger.warning("embed truncate tiktoken failed: %s — char fallback", e)
        if len(raw) <= _FALLBACK_CHAR_LIMIT:
            return raw
        out = raw[:_FALLBACK_CHAR_LIMIT].rstrip()
        logger.info(
            "embed query truncated (chars) %s→%s",
            len(raw),
            len(out),
        )
        return out


def make_openai_embedding_function(
    *,
    api_key: str,
    model_name: str = "text-embedding-3-small",
):
    if not api_key or not str(api_key).strip():
        raise ValueError("openai_api_key is required for embeddings")
    return embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key.strip(),
        model_name=model_name,
    )
