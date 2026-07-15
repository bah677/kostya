"""Тесты read-only RAG HTTP API."""

from __future__ import annotations

import secrets
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config import Config
from services.rag_read_api.app import create_app


def _test_cfg() -> Config:
    return Config(
        MIRON_BOT_TOKEN="test:token",
        OPENAI_API_KEY="sk-test",
        RAG_ENABLED=True,
        RAG_CHROMA_PERSIST_DIR="/tmp/chroma",
        RAG_READ_API_TOKEN="test-read-token",
    )


@pytest.fixture
def client():
    mock_stack = MagicMock()
    mock_stack.expert_count_golden_count.return_value = (10, 2)
    mock_stack.vectors.expert_collection = MagicMock()
    mock_stack.vectors.golden_collection = MagicMock()

    with patch("services.rag_read_api.app.try_build_rag_stack", return_value=mock_stack):
        with patch(
            "services.rag_read_api.app.query_expert_collection",
            return_value={
                "query": "молитва",
                "top_k": 3,
                "where": None,
                "chunks": [{"id": "1", "text": "chunk", "metadata": {}, "formatted": "[x]\nchunk"}],
                "context_text": "[x]\nchunk",
            },
        ):
            app = create_app(cfg=_test_cfg())
            with TestClient(app) as tc:
                yield tc


def test_health_without_auth(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_meta_requires_token(client: TestClient):
    assert client.get("/v1/meta").status_code == 401
    r = client.get("/v1/meta", headers={"Authorization": "Bearer test-read-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["read_only"] is True
    assert body["expert_count"] == 10


def test_expert_query(client: TestClient):
    r = client.post(
        "/v1/query/expert",
        headers={"Authorization": "Bearer test-read-token"},
        json={"query": "молитва", "top_k": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["collection"] == "expert_materials"
    assert len(body["chunks"]) == 1
    assert "context_text" in body


def test_invalid_token(client: TestClient):
    r = client.post(
        "/v1/query/expert",
        headers={"Authorization": "Bearer wrong"},
        json={"query": "x"},
    )
    assert r.status_code == 403
