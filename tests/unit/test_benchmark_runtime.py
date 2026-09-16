"""Adapters must call the real service path and retain operational failures."""

from __future__ import annotations

from dataclasses import asdict, replace

from src.core.settings import load_settings
from src.core.types import RetrievalResult
from src.observability.evaluation.benchmark_runtime import (
    BenchmarkAdapter,
    isolated_settings,
    sanitized,
)


def test_service_adapter_instruments_actual_tool(monkeypatch):
    from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

    class Search:
        def search(self, **kwargs):
            return [
                RetrievalResult(
                    chunk_id="c",
                    text="source evidence",
                    score=0.8,
                    metadata={"source_path": "paper.pdf", "source_ref": "p", "page": 2},
                )
            ]

    def init(tool, collection):
        tool._hybrid_search = Search()
        tool._reranker = None
        tool._vector_store = None
        tool._section_store = None

    monkeypatch.setattr(QueryKnowledgeHubTool, "_ensure_initialized", init)
    monkeypatch.setattr(
        "src.mcp_server.tools.query_knowledge_hub.TraceCollector.collect", lambda *args: None
    )
    settings = load_settings()
    adapter = BenchmarkAdapter.__new__(BenchmarkAdapter)
    adapter.settings = replace(settings, rerank=replace(settings.rerank, enabled=False))
    adapter.collection, adapter.tools = "fixture", {}
    adapter.lookup = {"c": {"paper_id": "paperhash"}}
    response = adapter.service({"query": "Find evidence"}, 5, {"expand_context": "none"})
    assert response["evidence"][0]["paper_id"] == "paperhash"
    assert response["evidence"][0]["page_start"] == 2
    assert response["raw_response"]["structuredContent"]["evidenceBundle"]
    assert response["candidates"][0]["chunk_id"] == "c"
    assert response["trace"]["stages"]

    def broken(**kwargs):
        raise RuntimeError("index unavailable")

    adapter.tools["service"]._hybrid_search.search = broken
    # Do not reset the injected broken search on the second request.
    monkeypatch.setattr(QueryKnowledgeHubTool, "_ensure_initialized", lambda *args: None)
    response = adapter.service({"query": "Find evidence"}, 5, {"expand_context": "none"})
    assert response["evidence"] == []
    assert response["observation_errors"] == ["index unavailable"]


def test_isolated_profile_does_not_change_production(tmp_path):
    original = load_settings()
    settings = isolated_settings(tmp_path)
    assert settings.vector_store.persist_directory != original.vector_store.persist_directory
    assert str(tmp_path) in settings.vector_store.persist_directory
    assert settings.ingestion.chunk_refiner["use_llm"] is False
    assert settings.embedding.model == original.embedding.model
    assert sanitized(asdict(load_settings())) == sanitized(asdict(original))
