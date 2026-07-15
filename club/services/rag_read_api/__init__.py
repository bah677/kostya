"""Read-only HTTP API для удалённого доступа к Chroma RAG."""

from services.rag_read_api.app import create_app

__all__ = ["create_app"]
