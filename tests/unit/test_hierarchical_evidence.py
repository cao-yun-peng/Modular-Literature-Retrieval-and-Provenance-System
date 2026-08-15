"""Tests for Parent–Child recovery, Evidence Bundle and Zotero handoff."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.core.query_engine.context_expander import ContextExpander
from src.core.query_engine.evidence_deduplicator import EvidenceDeduplicator
from src.core.query_engine.fulltext_handoff_policy import FulltextHandoffPolicy
from src.core.response.citation_generator import CitationGenerator
from src.core.response.evidence_bundle import EvidenceBundleBuilder
from src.core.settings import AgentHandoffSettings
from src.core.types import Chunk, Document, RetrievalResult
from src.ingestion.chunking.hierarchical_chunker import HierarchicalDocumentChunker
from src.ingestion.storage.section_store import SectionStore


class _FakeBaseChunker:
    def split_document(self, document: Document) -> list[Chunk]:
        return [
            Chunk(
                id="child-1",
                text="first evidence",
                metadata={"source_path": "paper.pdf", "source_ref": document.id, "section": "Results", "page": 7},
            ),
            Chunk(
                id="child-2",
                text="second evidence",
                metadata={"source_path": "paper.pdf", "source_ref": document.id, "section": "Results", "page": 8},
            ),
            Chunk(
                id="child-3",
                text="method evidence",
                metadata={"source_path": "paper.pdf", "source_ref": document.id, "section": "Methods", "page": 3},
            ),
        ]


def _hierarchical_split():
    chunker = object.__new__(HierarchicalDocumentChunker)
    chunker._base = _FakeBaseChunker()
    chunker.parent_size = 100
    chunker.schema_version = "2.0"
    chunker.last_parents = []
    document = Document(
        id="doc-1",
        text="unused",
        metadata={
            "source_path": "paper.pdf",
            "source_type": "zotero",
            "zotero_item_key": "ITEM1",
            "zotero_attachment_key": "ATTACH1",
        },
    )
    return chunker, chunker.split_document(document)


def test_hierarchical_chunker_adds_parent_and_neighbor_relations() -> None:
    chunker, children = _hierarchical_split()

    assert len(chunker.last_parents) == 2
    assert children[0].metadata["parent_id"] == children[1].metadata["parent_id"]
    assert children[2].metadata["parent_id"] != children[1].metadata["parent_id"]
    assert children[0].metadata["next_chunk_id"] == "child-2"
    assert children[1].metadata["previous_chunk_id"] == "child-1"
    assert children[0].metadata["page_start"] == 7
    assert children[0].metadata["page_end"] == 8


def test_section_store_round_trip_and_context_expansion(tmp_path: Path) -> None:
    chunker, children = _hierarchical_split()
    store = SectionStore(tmp_path / "sections.sqlite3")
    store.upsert("papers-v2", chunker.last_parents, children, "2.0")

    parent = store.get_parent_for_child("papers-v2", "child-1")
    assert parent is not None
    assert parent.child_ids == ("child-1", "child-2")
    assert store.get_neighbor_ids("papers-v2", "child-2") == ("child-1", "child-3")

    result = RetrievalResult(
        chunk_id="child-1",
        score=0.03,
        text="first evidence",
        metadata=children[0].metadata,
    )
    expanded = ContextExpander(store, None, 1000).expand(
        [result], mode="parent", collection="papers-v2"
    )
    assert expanded[0].text == "first evidence"
    assert expanded[0].metadata["expanded_context"]["type"] == "parent"
    assert "second evidence" in expanded[0].metadata["expanded_context"]["text"]


def test_deduplicator_caps_one_parent_without_changing_order() -> None:
    results = [
        RetrievalResult(
            chunk_id=f"c{i}",
            score=1.0 - i / 10,
            text=f"evidence {i}",
            metadata={"parent_id": "p1" if i < 3 else "p2"},
        )
        for i in range(4)
    ]
    kept = EvidenceDeduplicator(max_per_parent=2).deduplicate(results, top_k=4)
    assert [result.chunk_id for result in kept] == ["c0", "c1", "c3"]


def test_global_reading_returns_optional_zotero_fulltext_handoff() -> None:
    result = RetrievalResult(
        chunk_id="c1",
        score=0.03,
        text="evidence",
        metadata={
            "source_path": "paper.pdf",
            "zotero_item_key": "ITEM1",
            "zotero_attachment_key": "ATTACH1",
            "zotero_title": "Paper",
        },
    )
    decision = FulltextHandoffPolicy(AgentHandoffSettings(enabled=True)).decide(
        "请总结这篇论文的主要贡献和整体论证", [result]
    )

    assert decision.signal == "needs_fulltext"
    assert decision.action_dict()["zotero_attachment_key"] == "ATTACH1"
    assert decision.action_dict()["required"] is False
    assert decision.action_dict()["project_did_not_fetch_fulltext"] is True


def test_evidence_bundle_keeps_original_text_and_source_identity() -> None:
    result = RetrievalResult(
        chunk_id="c1",
        score=0.031,
        text="verbatim evidence",
        metadata={
            "source_path": "paper.pdf",
            "source_ref": "doc-1",
            "parent_id": "parent-1",
            "section_path": "Results,Weak confinement",
            "page_start": 7,
            "page_end": 8,
            "zotero_item_key": "ITEM1",
            "zotero_attachment_key": "ATTACH1",
            "citation_key": "paper_2025",
        },
    )
    citations = CitationGenerator().generate([result])
    decision = FulltextHandoffPolicy(AgentHandoffSettings(enabled=True)).decide(
        "局部事实是什么", [result]
    )
    bundle = EvidenceBundleBuilder().build(
        query="局部事实是什么",
        collection="papers-v2",
        requested_mode="evidence",
        selected_mode="evidence",
        results=[result],
        citations=citations,
        decision=decision,
    )

    evidence = bundle["evidence"][0]
    assert bundle["schema_version"] == "1.0"
    assert evidence["text"] == "verbatim evidence"
    assert evidence["page_start"] == 7
    assert evidence["zotero_attachment_key"] == "ATTACH1"
    assert bundle["citations"][0]["citation_key"] == "paper_2025"
    assert bundle["citations"][0]["locator"] == "pp. 7–8"
    assert bundle["citations"][0]["markdown"] == "[@paper_2025, pp. 7–8]"


def test_query_tool_schema_additions_are_optional() -> None:
    from src.mcp_server.tools.query_knowledge_hub import TOOL_INPUT_SCHEMA

    assert TOOL_INPUT_SCHEMA["required"] == ["query"]
    assert set(
        (
            "retrieval_mode",
            "document_ids",
            "zotero_item_keys",
            "expand_context",
            "allow_fulltext_handoff",
        )
    ).issubset(TOOL_INPUT_SCHEMA["properties"])


@pytest.mark.asyncio
async def test_legacy_query_call_returns_versioned_evidence_bundle(monkeypatch) -> None:
    import src.mcp_server.tools.query_knowledge_hub as query_module
    from src.core.response.response_builder import ResponseBuilder
    from src.core.settings import load_settings
    from src.mcp_server.tools.query_knowledge_hub import (
        QueryKnowledgeHubConfig,
        QueryKnowledgeHubTool,
    )

    class _Hybrid:
        def search(self, **kwargs):
            return [
                RetrievalResult(
                    chunk_id="c1",
                    score=0.03,
                    text="legacy evidence",
                    metadata={"source_path": "manual.pdf", "source_ref": "doc-1"},
                )
            ]

    class _Collector:
        def collect(self, trace):
            return None

    settings = load_settings()
    settings = replace(
        settings,
        observability=replace(settings.observability, trace_enabled=False),
    )
    tool = QueryKnowledgeHubTool(
        settings=settings,
        config=QueryKnowledgeHubConfig(enable_rerank=False),
        hybrid_search=_Hybrid(),
        response_builder=ResponseBuilder(enable_multimodal=False),
    )
    tool._initialized = True
    tool._current_collection = "papers"
    monkeypatch.setattr(query_module, "TraceCollector", lambda: _Collector())

    response = await tool.execute("legacy query", top_k=5, collection="papers")

    assert response.is_empty is False
    assert response.evidence_bundle["schema_version"] == "1.0"
    assert response.evidence_bundle["evidence"][0]["text"] == "legacy evidence"
    assert response.evidence_bundle["recommended_next_action"] is None


def test_conservative_fusion_keeps_rrf_and_rerank_score_breakdown() -> None:
    from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

    original = [
        RetrievalResult(chunk_id="a", score=0.03, text="a"),
        RetrievalResult(chunk_id="b", score=0.02, text="b"),
        RetrievalResult(chunk_id="c", score=0.01, text="c"),
    ]
    reranked = [
        RetrievalResult(chunk_id="b", score=0.9, text="b"),
        RetrievalResult(chunk_id="a", score=0.1, text="a"),
    ]
    fused = QueryKnowledgeHubTool._conservative_fusion(
        original, reranked, top_k=3, rrf_weight=0.7
    )

    assert {result.chunk_id for result in fused} == {"a", "b", "c"}
    assert all(result.metadata["rerank_strategy"] == "conservative" for result in fused)
    assert all("original_score" in result.metadata for result in fused)


def test_document_summary_can_resolve_zotero_item_without_exposing_path() -> None:
    from src.mcp_server.tools.get_document_summary import (
        GetDocumentSummaryConfig,
        GetDocumentSummaryTool,
    )

    class _Collection:
        def get(self, where=None, include=None):
            assert where == {"zotero_item_key": "ITEM1"}
            return {
                "ids": ["c1"],
                "documents": ["paper evidence"],
                "metadatas": [
                    {
                        "source_ref": "doc-1",
                        "source_path": "C:/private/paper.pdf",
                        "source_type": "zotero",
                        "title": "Paper",
                        "zotero_item_key": "ITEM1",
                        "zotero_attachment_key": "ATTACH1",
                    }
                ],
            }

    tool = GetDocumentSummaryTool(
        config=GetDocumentSummaryConfig(default_collection="papers")
    )
    tool._get_collection = lambda collection=None: _Collection()
    summary = tool.get_document_summary(zotero_item_key="ITEM1")

    assert summary.doc_id == "doc-1"
    assert summary.source_path is None
    assert summary.metadata["can_handoff_to_zotero_fulltext"] is True
