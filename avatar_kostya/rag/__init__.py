"""
Изолированный слой RAG (Chroma + эмбеддинги + retrieval + золотой фонд).

Не импортирует bot/*, storage/*, openai_client/*. Настройки передаются через
`RAGSettings`; инициализация — `build_rag_stack()` из `rag.runtime`.

Копирование в другой проект с той же идеологией: перенести каталог ``rag/``,
добавить зависимости (``chromadb``, ``tiktoken``), при старте приложения
вызвать ``build_rag_stack(RAGSettings(...))`` и при необходимости повесить
хендлеры Telegram на методы ретривера / индексатора.
"""

from rag.golden_store import GoldenExamplesStore
from rag.material_index import MaterialIndexService
from rag.retriever import ExpertRetriever
from rag.runtime import RagStack, build_rag_stack
from rag.settings import RAGSettings
from rag.vector_store import VectorStoreService

__all__ = [
    "RAGSettings",
    "RagStack",
    "build_rag_stack",
    "VectorStoreService",
    "ExpertRetriever",
    "GoldenExamplesStore",
    "MaterialIndexService",
]
