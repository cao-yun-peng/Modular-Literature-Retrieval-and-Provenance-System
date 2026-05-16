"""Unit tests for linked asset resolution and response rendering.

Covers the full linked-retrieval chain:

1. ``_parse_metadata_list`` — ChromaDB serialization round-trip
2. ``_resolve_linked_assets`` — vector store lookup for figure/table chunks
3. ``ResponseBuilder._format_linked_assets`` — markdown rendering
4. ``ResponseBuilder.build()`` — full response with linked_assets parameter
5. ``ChromaStore.get_by_metadata`` — metadata-based chunk retrieval
"""

import pytest
from unittest.mock import Mock, MagicMock
from typing import Dict, Any, List

from src.core.types import RetrievalResult
from src.core.response.response_builder import ResponseBuilder, MCPToolResponse


# =============================================================================
# _parse_metadata_list tests
# =============================================================================

class TestParseMetadataList:
    """Tests for QueryKnowledgeHubTool._parse_metadata_list()."""

    @pytest.fixture
    def tool(self):
        from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool
        return QueryKnowledgeHubTool()

    def test_parses_list_value(self, tool):
        result = tool._parse_metadata_list(["fig_0", "fig_1"])
        assert result == ["fig_0", "fig_1"]

    def test_parses_comma_separated_string(self, tool):
        result = tool._parse_metadata_list("fig_0,fig_1")
        assert result == ["fig_0", "fig_1"]

    def test_parses_single_string(self, tool):
        result = tool._parse_metadata_list("fig_0")
        assert result == ["fig_0"]

    def test_parses_string_with_spaces(self, tool):
        result = tool._parse_metadata_list("fig_0, fig_1")
        assert result == ["fig_0", "fig_1"]

    def test_parses_empty_list(self, tool):
        assert tool._parse_metadata_list([]) == []

    def test_parses_empty_string(self, tool):
        assert tool._parse_metadata_list("") == []

    def test_parses_none(self, tool):
        assert tool._parse_metadata_list(None) == []

    def test_parses_whitespace_string(self, tool):
        assert tool._parse_metadata_list("   ") == []


# =============================================================================
# _resolve_linked_assets tests
# =============================================================================

class TestResolveLinkedAssets:
    """Tests for QueryKnowledgeHubTool._resolve_linked_assets()."""

    @pytest.fixture
    def tool(self):
        from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool
        t = QueryKnowledgeHubTool()
        return t

    def _make_results(self) -> List[RetrievalResult]:
        """Build retrieval results with linked_figures / linked_tables."""
        return [
            RetrievalResult(
                chunk_id="chunk_body_001",
                score=0.95,
                text="Body text referencing Figure 1 and Table 1.",
                metadata={
                    "source_path": "papers/test.pdf",
                    "source_ref": "doc_paper_001",
                    "section": "Introduction",
                    "linked_figures": "fig_0,fig_1",
                    "linked_tables": "tab_0",
                },
            ),
            RetrievalResult(
                chunk_id="chunk_body_002",
                score=0.80,
                text="Another body paragraph with no references.",
                metadata={
                    "source_path": "papers/test.pdf",
                    "source_ref": "doc_paper_001",
                    "section": "Methods",
                },
            ),
        ]

    def test_returns_empty_when_no_vector_store(self, tool):
        results = self._make_results()
        tool._vector_store = None
        assert tool._resolve_linked_assets(results) == {}

    def test_returns_empty_when_no_get_by_metadata(self, tool):
        results = self._make_results()
        tool._vector_store = Mock()  # no get_by_metadata attr
        assert tool._resolve_linked_assets(results) == {}

    def test_returns_empty_when_no_linked_refs(self, tool):
        results = [
            RetrievalResult(
                chunk_id="chunk_plain",
                score=0.9,
                text="Plain text.",
                metadata={"source_path": "test.pdf", "source_ref": "doc_1"},
            )
        ]
        fake_store = Mock()
        fake_store.get_by_metadata = Mock()
        tool._vector_store = fake_store
        assert tool._resolve_linked_assets(results) == {}
        fake_store.get_by_metadata.assert_not_called()

    def test_resolves_figures_from_vector_store(self, tool):
        results = self._make_results()
        fake_store = Mock()

        def fake_get_by_metadata(filters):
            if filters.get("figure_id") == "fig_0":
                return [{"id": "chunk_fig_0", "text": "[FIGURE: fig_0]\nCaption: Fig 1\nDescription: desc",
                         "metadata": {"figure_id": "fig_0", "figure_caption": "Fig 1"}}]
            if filters.get("figure_id") == "fig_1":
                return [{"id": "chunk_fig_1", "text": "[FIGURE: fig_1]\nCaption: Fig 2\nDescription: desc2",
                         "metadata": {"figure_id": "fig_1", "figure_caption": "Fig 2"}}]
            return []

        fake_store.get_by_metadata = fake_get_by_metadata
        tool._vector_store = fake_store

        linked = tool._resolve_linked_assets(results)
        assert "chunk_body_001" in linked
        assert len(linked["chunk_body_001"]["figures"]) == 2
        assert linked["chunk_body_001"]["figures"][0]["figure_id"] == "fig_0"
        assert linked["chunk_body_001"]["figures"][1]["figure_id"] == "fig_1"

    def test_resolves_tables_from_vector_store(self, tool):
        results = self._make_results()
        fake_store = Mock()

        def fake_get_by_metadata(filters):
            if filters.get("table_id") == "tab_0":
                return [{"id": "chunk_tab_0", "text": "[TABLE_DATA: tab_0]\nCaption: Tab 1\nContent:\nA\tB",
                         "metadata": {"table_id": "tab_0", "table_caption": "Tab 1"}}]
            return []

        fake_store.get_by_metadata = fake_get_by_metadata
        tool._vector_store = fake_store

        linked = tool._resolve_linked_assets(results)
        assert "chunk_body_001" in linked
        assert len(linked["chunk_body_001"]["tables"]) == 1
        assert linked["chunk_body_001"]["tables"][0]["table_id"] == "tab_0"

    def test_filters_by_source_ref(self, tool):
        """Linked asset lookup scoped to the document via source_ref."""
        results = self._make_results()
        fake_store = Mock()
        received_filters = []

        def fake_get_by_metadata(filters):
            received_filters.append(dict(filters))
            return []

        fake_store.get_by_metadata = fake_get_by_metadata
        tool._vector_store = fake_store

        tool._resolve_linked_assets(results)
        # All queries should include source_ref
        for f in received_filters:
            assert "source_ref" in f
            assert f["source_ref"] == "doc_paper_001"

    def test_graceful_error_on_lookup_failure(self, tool):
        results = self._make_results()
        fake_store = Mock()
        fake_store.get_by_metadata = Mock(side_effect=RuntimeError("DB error"))
        tool._vector_store = fake_store

        # Should not raise — returns empty for the failed chunk
        linked = tool._resolve_linked_assets(results)
        assert linked == {}  # All lookups failed

    def test_parses_list_linked_figures(self, tool):
        """linked_figures as a Python list (pre-ChromaDB serialization)."""
        results = [
            RetrievalResult(
                chunk_id="c1", score=0.9, text="t",
                metadata={"source_path": "t.pdf", "source_ref": "d1",
                          "linked_figures": ["fig_0", "fig_1"]},
            )
        ]
        fake_store = Mock()
        fake_store.get_by_metadata = Mock(return_value=[
            {"id": "cf0", "text": "[FIGURE: fig_0]", "metadata": {"figure_id": "fig_0"}}
        ])
        tool._vector_store = fake_store

        linked = tool._resolve_linked_assets(results)
        assert "c1" in linked
        # Both fig_0 and fig_1 looked up
        assert fake_store.get_by_metadata.call_count == 2


# =============================================================================
# ResponseBuilder._format_linked_assets tests
# =============================================================================

class TestFormatLinkedAssets:
    """Tests for ResponseBuilder._format_linked_assets()."""

    @pytest.fixture
    def builder(self):
        return ResponseBuilder()

    def test_renders_figures_with_caption(self, builder):
        assets = {
            "figures": [
                {"figure_id": "fig_0", "caption": "Figure 1: Overview",
                 "text": "[FIGURE: fig_0]\nCaption: Figure 1: Overview\nDescription: An architecture diagram."},
            ],
            "tables": [],
        }
        lines = builder._format_linked_assets(assets)
        text = "\n".join(lines)
        assert "<details>" in text
        assert "1 figures" in text
        assert "[FIG_REF: fig_0]" in text
        assert "Figure 1: Overview" in text
        assert "An architecture diagram" in text

    def test_renders_tables_with_content(self, builder):
        assets = {
            "figures": [],
            "tables": [
                {"table_id": "tab_0", "caption": "Table 1: Results",
                 "text": "[TABLE_DATA: tab_0]\nCaption: Table 1: Results\nContent:\nA\tB\n1\t2"},
            ],
        }
        lines = builder._format_linked_assets(assets)
        text = "\n".join(lines)
        assert "<details>" in text
        assert "1 tables" in text
        assert "[TABLE_REF: tab_0]" in text
        assert "Table 1: Results" in text
        assert "A\tB" in text

    def test_renders_both_figures_and_tables(self, builder):
        assets = {
            "figures": [{"figure_id": "fig_0", "caption": "Fig 1", "text": "[FIGURE: fig_0]"}],
            "tables": [{"table_id": "tab_0", "caption": "Tab 1", "text": "[TABLE_DATA: tab_0]"}],
        }
        lines = builder._format_linked_assets(assets)
        text = "\n".join(lines)
        assert "1 figures" in text
        assert "1 tables" in text

    def test_renders_empty_when_no_assets(self, builder):
        lines = builder._format_linked_assets({"figures": [], "tables": []})
        assert lines == []

    def test_renders_multiple_figures(self, builder):
        assets = {
            "figures": [
                {"figure_id": "fig_0", "caption": "Fig 1", "text": "[FIGURE: fig_0]"},
                {"figure_id": "fig_1", "caption": "Fig 2", "text": "[FIGURE: fig_1]"},
            ],
            "tables": [],
        }
        lines = builder._format_linked_assets(assets)
        text = "\n".join(lines)
        assert "2 figures" in text
        assert "[FIG_REF: fig_0]" in text
        assert "[FIG_REF: fig_1]" in text


# =============================================================================
# ResponseBuilder.build() with linked_assets
# =============================================================================

class TestResponseBuilderWithLinkedAssets:
    """Tests for ResponseBuilder.build() with linked_assets parameter."""

    @pytest.fixture
    def builder(self):
        return ResponseBuilder()

    @pytest.fixture
    def sample_results(self) -> List[RetrievalResult]:
        return [
            RetrievalResult(
                chunk_id="chunk_001",
                score=0.95,
                text="Body text about the system architecture [FIG_REF: fig_0].",
                metadata={
                    "source_path": "papers/test.pdf",
                    "section": "Introduction",
                    "linked_figures": "fig_0",
                },
            ),
        ]

    @pytest.fixture
    def linked_assets(self) -> Dict[str, Any]:
        return {
            "chunk_001": {
                "figures": [
                    {"figure_id": "fig_0", "caption": "Figure 1: Architecture",
                     "text": "[FIGURE: fig_0]\nCaption: Figure 1: Architecture\nDescription: System overview."},
                ],
                "tables": [],
            },
        }

    def test_build_includes_linked_assets_in_content(self, builder, sample_results, linked_assets):
        response = builder.build(
            results=sample_results,
            query="system architecture",
            linked_assets=linked_assets,
        )
        assert "<details>" in response.content
        assert "[FIG_REF: fig_0]" in response.content
        assert "Figure 1: Architecture" in response.content

    def test_build_adds_linked_asset_metadata(self, builder, sample_results, linked_assets):
        response = builder.build(
            results=sample_results,
            query="test",
            linked_assets=linked_assets,
        )
        assert response.metadata["has_linked_assets"] is True
        assert response.metadata["linked_asset_count"] == 1

    def test_build_without_linked_assets_no_extra_metadata(self, builder, sample_results):
        response = builder.build(
            results=sample_results,
            query="test",
        )
        assert "has_linked_assets" not in response.metadata
        assert "<details>" not in response.content

    def test_build_empty_linked_assets_dict(self, builder, sample_results):
        response = builder.build(
            results=sample_results,
            query="test",
            linked_assets={},
        )
        assert "has_linked_assets" not in response.metadata

    def test_build_linked_assets_for_different_chunk_not_included(self, builder, sample_results):
        """Only chunks with matching chunk_id get their linked assets rendered."""
        linked = {
            "other_chunk_id": {
                "figures": [{"figure_id": "fig_99", "caption": "Other", "text": "other"}],
                "tables": [],
            },
        }
        response = builder.build(
            results=sample_results,
            query="test",
            linked_assets=linked,
        )
        # chunk_001 has no entry in linked → no <details> in content
        assert "<details>" not in response.content
        # But metadata should still be correct
        assert response.metadata.get("has_linked_assets") is True

    def test_citations_still_present_with_linked_assets(self, builder, sample_results, linked_assets):
        response = builder.build(
            results=sample_results,
            query="test",
            linked_assets=linked_assets,
        )
        assert len(response.citations) == 1
        assert response.citations[0].chunk_id == "chunk_001"


# =============================================================================
# ChromaStore.get_by_metadata (structural / unit)
# =============================================================================

class TestChromaStoreGetByMetadata:
    """Tests for ChromaStore.get_by_metadata() with mocked ChromaDB collection."""

    @pytest.fixture
    def store(self):
        """Create a ChromaStore mock with a real _build_where_clause."""
        store = Mock()
        store.collection = Mock()
        # _build_where_clause is an instance method — bind it properly
        from src.libs.vector_store.chroma_store import ChromaStore
        store._build_where_clause = lambda filters: ChromaStore._build_where_clause(store, filters)
        store.get_by_metadata = ChromaStore.get_by_metadata.__get__(store, ChromaStore)
        return store

    def test_empty_filters_returns_empty(self, store):
        assert store.get_by_metadata({}) == []

    def test_builds_where_clause_and_queries(self, store):
        store.collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["text1"],
            "metadatas": [{"k": "v"}],
        }
        results = store.get_by_metadata({"figure_id": "fig_0"})
        assert len(results) == 1
        assert results[0]["id"] == "id1"
        assert results[0]["text"] == "text1"
        assert results[0]["metadata"] == {"k": "v"}

    def test_returns_empty_on_chromadb_error(self, store):
        store.collection.get.side_effect = RuntimeError("ChromaDB down")
        results = store.get_by_metadata({"figure_id": "fig_0"})
        assert results == []

    def test_handles_missing_documents_and_metadatas(self, store):
        store.collection.get.return_value = {
            "ids": ["id1"],
        }
        results = store.get_by_metadata({"figure_id": "fig_0"})
        assert len(results) == 1
        assert results[0]["text"] == ""
        assert results[0]["metadata"] == {}


# =============================================================================
# End-to-end linked retrieval flow (component integration)
# =============================================================================

class TestLinkedRetrievalE2E:
    """Integration-style tests for the full linked-retrieval chain."""

    def test_full_chain_parse_and_resolve(self):
        """Parse metadata → resolve → build response — full chain."""
        from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

        tool = QueryKnowledgeHubTool()

        # 1. Simulate parsed linked_figures from ChromaDB metadata
        raw_figs = "fig_0,fig_1"
        parsed = tool._parse_metadata_list(raw_figs)
        assert parsed == ["fig_0", "fig_1"]

        # 2. Mock vector store resolution
        fake_store = Mock()

        def fake_get_by_metadata(filters):
            fid = filters.get("figure_id", "")
            return [{"id": f"chunk_{fid}", "text": f"[FIGURE: {fid}]",
                     "metadata": {"figure_id": fid, "figure_caption": f"Caption for {fid}"}}]

        fake_store.get_by_metadata = fake_get_by_metadata
        tool._vector_store = fake_store

        results = [
            RetrievalResult(
                chunk_id="body_1", score=0.9, text="Body text.",
                metadata={"source_path": "test.pdf", "source_ref": "doc_1",
                          "linked_figures": parsed},
            )
        ]
        linked = tool._resolve_linked_assets(results)
        assert "body_1" in linked
        assert len(linked["body_1"]["figures"]) == 2

        # 3. Build response with linked assets
        builder = ResponseBuilder()
        response = builder.build(
            results=results, query="test", linked_assets=linked,
        )
        assert "<details>" in response.content
        assert "[FIG_REF: fig_0]" in response.content
        assert "[FIG_REF: fig_1]" in response.content
        assert response.metadata["has_linked_assets"] is True
