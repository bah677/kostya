"""Read-only HTTP API для удалённого semantic search по Chroma RAG."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from bot.integrations.rag_bridge import try_build_rag_stack
from config import Config, config as default_config
from rag.runtime import RagStack
from services.rag_read_api.chroma_query import (
    query_expert_collection,
    query_golden_collection,
)

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(5, ge=1, le=20)
    where: Optional[Dict[str, Any]] = None


class ExpertQueryResponse(BaseModel):
    collection: str
    embedding_model: str
    query: str
    top_k: int
    where: Optional[Dict[str, Any]] = None
    chunks: list[Dict[str, Any]]
    context_text: str


class GoldenQueryResponse(BaseModel):
    collection: str
    embedding_model: str
    query: str
    top_k: int
    where: Optional[Dict[str, Any]] = None
    examples: list[Dict[str, Any]]
    few_shot_text: str


class MetaResponse(BaseModel):
    chroma_path: str
    embedding_model: str
    expert_collection: str
    golden_collection: str
    expert_count: int
    golden_count: int
    read_only: bool = True


def _load_cfg(env_file: Optional[str]) -> Config:
    if not env_file:
        return default_config
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    from config import load_config

    return load_config()


def _check_token(cfg: Config, credentials: Optional[HTTPAuthorizationCredentials]) -> None:
    expected = (cfg.RAG_READ_API_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="RAG read API token is not configured")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


def create_app(
    *,
    cfg: Optional[Config] = None,
    env_file: Optional[str] = None,
) -> FastAPI:
    app_cfg = cfg or _load_cfg(env_file)
    stack_holder: dict[str, Optional[RagStack]] = {"stack": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not app_cfg.RAG_ENABLED:
            logger.error("RAG_ENABLED=0 — API не может стартовать без RAG")
            yield
            return
        token = (app_cfg.RAG_READ_API_TOKEN or "").strip()
        if not token:
            logger.error("RAG_READ_API_TOKEN пуст — задайте длинный секрет в .env")
            yield
            return
        stack = try_build_rag_stack(app_cfg)
        if stack is None:
            logger.error("Не удалось подключить read-only RAG stack")
            yield
            return
        stack_holder["stack"] = stack
        ne, ng = stack.expert_count_golden_count()
        logger.info(
            "RAG read API ready (expert=%s golden=%s path=%s)",
            ne,
            ng,
            app_cfg.resolved_rag_chroma_persist_dir,
        )
        yield
        stack_holder["stack"] = None

    app = FastAPI(
        title="Club RAG Read API",
        version="1.0.0",
        description="Read-only semantic search по Chroma (expert_materials, golden_examples).",
        lifespan=lifespan,
    )

    def _require_stack() -> RagStack:
        stack = stack_holder["stack"]
        if stack is None:
            raise HTTPException(
                status_code=503,
                detail="RAG stack is not available (check RAG_ENABLED, token, chroma path)",
            )
        return stack

    async def auth_dep(
        credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
    ) -> None:
        _check_token(app_cfg, credentials)

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        stack = stack_holder["stack"]
        if stack is None:
            return {"ok": False, "rag": "unavailable"}
        ne, ng = stack.expert_count_golden_count()
        return {
            "ok": True,
            "rag": "ready",
            "expert_count": ne,
            "golden_count": ng,
        }

    @app.get("/v1/meta", response_model=MetaResponse, dependencies=[Depends(auth_dep)])
    async def meta() -> MetaResponse:
        stack = _require_stack()
        ne, ng = stack.expert_count_golden_count()
        return MetaResponse(
            chroma_path=str(app_cfg.resolved_rag_chroma_persist_dir),
            embedding_model=app_cfg.RAG_EMBEDDING_MODEL,
            expert_collection=app_cfg.RAG_EXPERT_COLLECTION,
            golden_collection=app_cfg.RAG_GOLDEN_COLLECTION,
            expert_count=ne,
            golden_count=ng,
        )

    @app.post(
        "/v1/query/expert",
        response_model=ExpertQueryResponse,
        dependencies=[Depends(auth_dep)],
    )
    async def query_expert(body: QueryRequest) -> ExpertQueryResponse:
        stack = _require_stack()
        result = await asyncio.to_thread(
            query_expert_collection,
            stack.vectors.expert_collection,
            query=body.query,
            top_k=body.top_k,
            where=body.where,
        )
        return ExpertQueryResponse(
            collection=app_cfg.RAG_EXPERT_COLLECTION,
            embedding_model=app_cfg.RAG_EMBEDDING_MODEL,
            **result,
        )

    @app.post(
        "/v1/query/golden",
        response_model=GoldenQueryResponse,
        dependencies=[Depends(auth_dep)],
    )
    async def query_golden(body: QueryRequest) -> GoldenQueryResponse:
        stack = _require_stack()
        result = await asyncio.to_thread(
            query_golden_collection,
            stack.vectors.golden_collection,
            query=body.query,
            top_k=body.top_k,
            where=body.where,
        )
        return GoldenQueryResponse(
            collection=app_cfg.RAG_GOLDEN_COLLECTION,
            embedding_model=app_cfg.RAG_EMBEDDING_MODEL,
            **result,
        )

    @app.get("/v1/expert/metadata/{field}", dependencies=[Depends(auth_dep)])
    async def expert_metadata_values(field: str, max_scan: int = 50_000) -> Dict[str, Any]:
        stack = _require_stack()
        f = (field or "").strip()
        if not f or len(f) > 64:
            raise HTTPException(status_code=400, detail="Invalid metadata field")
        values = await stack.retriever.distinct_expert_metadata_values_async(
            f,
            max_scan=min(max_scan, 50_000),
        )
        return {"field": f, "values": values, "count": len(values)}

    return app
