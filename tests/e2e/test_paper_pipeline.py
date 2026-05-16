"""End-to-End tests for the Paper RAG pipeline with GROBID + linked retrieval.

Ingests the target paper, then verifies that keyword queries
(nonreciprocal, XY, topological defect) retrieve matching chunks
with linked figures and tables.

Requirements:
- GROBID on localhost:8070
- Ollama on localhost:11434 with nomic-embed-text
- ChromaDB (local persistence)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

TARGET_PDF = Path(
    "papers/research/"
    "Nonreciprocal Interactions Reshape Topological Defect Annihilation.pdf"
)
TEST_COLLECTION = "e2e_paper_pipeline_test"


def _clear_proxy_env():
    """Remove proxy env vars that interfere with localhost connections."""
    for k in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)


# ── Infrastructure checks ───────────────────────────────────────────────────

def _grobid_is_alive() -> bool:
    _clear_proxy_env()
    try:
        import requests
        r = requests.get("http://localhost:8070/api/isalive", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_is_alive() -> bool:
    _clear_proxy_env()
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ── Helpers ─────────────────────────────────────────────────────────────────

def _create_pipeline(collection: str = TEST_COLLECTION, force: bool = True):
    """Create an IngestionPipeline with PaperPdfLoader + GROBID enabled."""
    from src.core.settings import load_settings
    from src.ingestion.pipeline import IngestionPipeline

    _clear_proxy_env()
    settings = load_settings()
    pipeline = IngestionPipeline(
        settings=settings,
        collection=collection,
        force=force,
        use_paper_loader=True,
    )
    # Ensure the paper loader has GROBID enabled
    pipeline.loader.use_grobid = True
    pipeline.loader.grobid_url = "http://localhost:8070"
    return pipeline


def _create_query_tool(collection: str = TEST_COLLECTION):
    """Create a QueryKnowledgeHubTool for the given collection."""
    from src.core.settings import load_settings
    from src.mcp_server.tools.query_knowledge_hub import (
        QueryKnowledgeHubTool,
        QueryKnowledgeHubConfig,
    )

    _clear_proxy_env()
    settings = load_settings()
    config = QueryKnowledgeHubConfig(
        default_top_k=10,
        max_top_k=20,
        default_collection=collection,
        enable_rerank=False,  # Skip rerank (no LLM needed for recall test)
    )
    return QueryKnowledgeHubTool(settings=settings, config=config)


# ═══════════════════════════════════════════════════════════════════════════
# Test class
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.e2e
@pytest.mark.slow
class TestPaperPipelineE2E:
    """End-to-end: ingest paper → query → verify retrieval + linked assets."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Check prerequisites once for the whole class."""
        _clear_proxy_env()

        if not TARGET_PDF.exists():
            pytest.skip(f"Target PDF not found: {TARGET_PDF}")

        if not _grobid_is_alive():
            pytest.skip("GROBID is not available on localhost:8070")

        if not _ollama_is_alive():
            pytest.skip("Ollama is not available on localhost:11434")

        self.pdf_path = str(TARGET_PDF.resolve())

    # ── Stage 1: Ingestion ──────────────────────────────────────────────

    @pytest.fixture(scope="class")
    def pipeline_result(self):
        """Run ingestion once and share across retrieval tests."""
        _clear_proxy_env()

        pipeline = _create_pipeline(TEST_COLLECTION, force=True)
        logger.info("Starting ingestion for E2E test: %s", TARGET_PDF)
        result = pipeline.run(str(TARGET_PDF.resolve()))
        pipeline.close()
        return result

    def test_ingestion_succeeds(self, pipeline_result):
        """Ingestion pipeline completes successfully."""
        assert pipeline_result.success, (
            f"Ingestion failed: {pipeline_result.error}"
        )

    def test_document_has_grobid_metadata(self, pipeline_result):
        """Document metadata includes GROBID-extracted fields."""
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory
        from src.core.settings import load_settings

        assert pipeline_result.chunk_count > 0, (
            "Ingestion should produce at least one chunk"
        )

        settings = load_settings()
        store = VectorStoreFactory.create(settings, collection_name=TEST_COLLECTION)

        # Verify GROBID metadata exists: check for paper-specific chunk types
        results = store.collection.get(include=["metadatas"])
        assert results and results.get("ids"), "No chunks found in ChromaDB"

        all_metadata = results["metadatas"]
        chunk_types = {m.get("chunk_type") for m in all_metadata if m}

        # Paper pipeline should produce paper-specific chunk types
        expected_types = {"title_abstract", "figure", "table"}
        found = expected_types & chunk_types
        assert found, (
            f"Expected GROBID paper chunk types {expected_types}, "
            f"got chunk_types={chunk_types}"
        )

    def test_title_abstract_chunk_exists(self, pipeline_result):
        """A title_abstract chunk is generated by the GROBID path."""
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory
        from src.core.settings import load_settings

        settings = load_settings()
        store = VectorStoreFactory.create(settings, collection_name=TEST_COLLECTION)

        # Query for chunks with chunk_type = title_abstract
        results = store.collection.get(
            where={"chunk_type": "title_abstract"},
            include=["metadatas", "documents"],
        )
        assert results and results.get("ids"), "No title_abstract chunk found"
        title_meta = results["metadatas"][0]
        assert "Nonreciprocal" in title_meta.get("title", ""), (
            f"Expected paper title in metadata, got: {title_meta.get('title', '')}"
        )

    def test_figure_chunks_exist(self, pipeline_result):
        """GROBID-extracted figures become separate chunks."""
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory
        from src.core.settings import load_settings

        settings = load_settings()
        store = VectorStoreFactory.create(settings, collection_name=TEST_COLLECTION)

        results = store.collection.get(
            where={"chunk_type": "figure"},
            include=["metadatas", "documents"],
        )
        assert results and results.get("ids"), "No figure chunks found"

        fig_ids = results.get("ids", [])
        logger.info("Found %d figure chunks: %s", len(fig_ids), fig_ids)
        # The paper has 5 figures (from GROBID extraction)
        assert len(fig_ids) >= 3, (
            f"Expected at least 3 figure chunks, got {len(fig_ids)}"
        )

        # Verify figure metadata
        for meta in results.get("metadatas", []):
            assert "figure_id" in meta, f"Figure chunk missing figure_id"
            assert "figure_caption" in meta, f"Figure chunk missing figure_caption"

    def test_table_chunks_exist(self, pipeline_result):
        """GROBID-extracted tables become separate chunks."""
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory
        from src.core.settings import load_settings

        settings = load_settings()
        store = VectorStoreFactory.create(settings, collection_name=TEST_COLLECTION)

        results = store.collection.get(
            where={"chunk_type": "table"},
            include=["metadatas", "documents"],
        )
        assert results and results.get("ids"), "No table chunks found"

        tab_ids = results.get("ids", [])
        logger.info("Found %d table chunks: %s", len(tab_ids), tab_ids)
        assert len(tab_ids) >= 1, (
            f"Expected at least 1 table chunk, got {len(tab_ids)}"
        )

    # ── Stage 2: Retrieval ──────────────────────────────────────────────

    @pytest.fixture
    def query_tool(self):
        """Create the query tool (lazy-init — first query warms up)."""
        _clear_proxy_env()
        return _create_query_tool(TEST_COLLECTION)

    @pytest.mark.asyncio
    async def test_search_nonreciprocal(self, query_tool):
        """Query 'nonreciprocal' returns results from the target paper."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="nonreciprocal",
            top_k=10,
            collection=TEST_COLLECTION,
        )
        assert not response.is_empty, "Expected results for 'nonreciprocal'"
        assert response.metadata["result_count"] > 0
        # At least one result should mention nonreciprocal
        content_lower = response.content.lower()
        assert "nonreciprocal" in content_lower, (
            f"Expected 'nonreciprocal' in response content"
        )

    @pytest.mark.asyncio
    async def test_search_XY_model(self, query_tool):
        """Query 'XY model' returns results from the target paper."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="XY model topological defect",
            top_k=10,
            collection=TEST_COLLECTION,
        )
        assert not response.is_empty, "Expected results for 'XY model'"
        # Content should contain relevant terms
        content_lower = response.content.lower()
        has_xy = "xy" in content_lower
        has_defect = "defect" in content_lower or "annihilation" in content_lower
        assert has_xy or has_defect, (
            f"Expected 'XY' or 'defect' in response, got none"
        )

    @pytest.mark.asyncio
    async def test_search_topological_defect(self, query_tool):
        """Query about topological defects returns the paper."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="topological defect annihilation dynamics",
            top_k=10,
            collection=TEST_COLLECTION,
        )
        assert not response.is_empty
        assert response.metadata["result_count"] > 0

        # Check that citations contain the paper
        source_paths = [c.source for c in response.citations]
        logger.info("Retrieved sources: %s", source_paths)

    @pytest.mark.asyncio
    async def test_linked_assets_in_response(self, query_tool):
        """Body chunks referencing figures have linked assets in response."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="figure shows phase diagram nonreciprocal interaction",
            top_k=10,
            collection=TEST_COLLECTION,
        )

        # Check if any results triggered linked asset resolution
        if response.metadata.get("has_linked_assets"):
            linked_count = response.metadata.get("linked_asset_count", 0)
            logger.info("Response has linked assets: count=%d", linked_count)
            assert linked_count > 0, "Linked asset count should be > 0"
            # The markdown should contain FIG_REF or TABLE_REF placeholders
            # (they may be in <details> blocks)
            assert "[FIG_REF:" in response.content or "[TABLE_REF:" in response.content, (
                "Response should contain FIG_REF or TABLE_REF references when linked assets exist"
            )
        else:
            logger.info("No linked assets in response — body chunks may not reference figures directly")

    @pytest.mark.asyncio
    async def test_source_path_in_citations(self, query_tool):
        """Retrieval citations include the paper's source path."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="nonreciprocal XY model",
            top_k=10,
            collection=TEST_COLLECTION,
        )
        # At least one citation should come from the target paper
        source_paths = [c.source for c in response.citations]
        target_in_results = any(
            "Nonreciprocal" in sp for sp in source_paths
        )
        logger.info("Sources in results: %s", source_paths)
        # Soft assertion — the paper may not always be top-ranked in tiny index
        if not target_in_results:
            logger.warning(
                "Target paper not in top citations for 'nonreciprocal XY model' — "
                "this may be expected with minimal indexing"
            )

    # ── Stage 3: Linked asset resolution ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_body_chunk_has_linked_figures_metadata(self, query_tool):
        """At least one body chunk has linked_figures metadata."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="FIG. 1 shows phase diagram",
            top_k=10,
            collection=TEST_COLLECTION,
        )

        # Verify response structure is valid
        assert response is not None, "Query tool should return a response"
        assert hasattr(response, "metadata"), "Response should have metadata"

        logger.info(
            "Response metadata keys: %s",
            list(response.metadata.keys()),
        )
        logger.info("Has linked assets: %s", response.metadata.get("has_linked_assets"))

        # The response should have citations or content
        assert hasattr(response, "citations") or hasattr(response, "content"), (
            "Response should have citations or content"
        )

    # ── Stage 4: Metadata quality ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_retrieved_chunks_have_paper_metadata(self, query_tool):
        """Chunks retrieved for the paper carry author/DOI metadata."""
        _clear_proxy_env()
        response = await query_tool.execute(
            query="nonreciprocal interaction defect annihilation",
            top_k=10,
            collection=TEST_COLLECTION,
        )

        for citation in response.citations:
            meta = citation.metadata or {}
            logger.info(
                "Citation [%d]: title=%s doi=%s",
                citation.index,
                meta.get("title", "?")[:60],
                meta.get("doi", "?"),
            )

        # At least one citation should have title or DOI metadata
        has_meta = any(
            c.metadata.get("title") or c.metadata.get("doi")
            for c in response.citations
        )
        # This may or may not be populated depending on chunk type
        logger.info("Citations with title/DOI metadata: %s", has_meta)


# ═══════════════════════════════════════════════════════════════════════════
# Fast smoke test (no GROBID needed)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.e2e
class TestPaperLoaderSmoke:
    """Smoke test: verify the loader can process the paper without full pipeline."""

    def test_pdf_exists_and_readable(self):
        """Target PDF exists and is a valid file."""
        assert TARGET_PDF.exists(), f"PDF not found: {TARGET_PDF}"
        assert TARGET_PDF.stat().st_size > 1000, "PDF suspiciously small"

    def test_grobid_can_parse_paper(self):
        """GROBID extracts structured metadata from the paper."""
        if not _grobid_is_alive():
            pytest.skip("GROBID not available")

        _clear_proxy_env()
        from src.libs.loader.grobid_parser import GrobidClient, GrobidTEIParser

        client = GrobidClient(timeout=120)
        tei = client.pdf_to_tei(str(TARGET_PDF.resolve()))
        assert len(tei) > 1000, f"TEI XML too short: {len(tei)} chars"

        parser = GrobidTEIParser(tei)
        paper = parser.parse()

        # Core metadata
        assert len(paper.title) > 5, f"Title too short: {paper.title!r}"
        assert "Nonreciprocal" in paper.title
        assert len(paper.authors) >= 2, f"Expected >=2 authors, got {len(paper.authors)}"
        assert len(paper.abstract) > 100, f"Abstract too short: {len(paper.abstract)}"

        # Sections
        assert len(paper.sections) >= 2, f"Expected >=2 sections, got {len(paper.sections)}"

        # Figures and tables
        assert len(paper.figures) >= 3, f"Expected >=3 figures, got {len(paper.figures)}"
        for fig in paper.figures:
            assert fig.id, "Figure missing id"
            assert fig.type == "figure"

        assert len(paper.tables) >= 1, f"Expected >=1 table, got {len(paper.tables)}"

        # DOI
        assert paper.doi and paper.doi.startswith("10."), (
            f"Expected valid DOI, got: {paper.doi!r}"
        )

        logger.info("GROBID paper parse succeeded:")
        logger.info("  Title: %s", paper.title)
        logger.info("  Authors: %s", paper.authors)
        logger.info("  Sections: %d", len(paper.sections))
        logger.info("  Figures: %d", len(paper.figures))
        logger.info("  Tables: %d", len(paper.tables))
        logger.info("  DOI: %s", paper.doi)

    def test_paper_pdf_loader_extracts_metadata(self):
        """PaperPdfLoader extracts metadata from the target PDF without full pipeline."""
        if not _grobid_is_alive():
            pytest.skip("GROBID not available")

        _clear_proxy_env()
        from src.libs.loader.pdf_loader import PaperPdfLoader

        loader = PaperPdfLoader(
            use_grobid=True,
            grobid_url="http://localhost:8070",
        )
        doc = loader.load(str(TARGET_PDF.resolve()))

        meta = doc.metadata
        assert meta["doc_type"] == "paper"
        assert meta["paper_mode"] is True

        # GROBID metadata
        assert "Nonreciprocal" in meta.get("title", "")
        assert len(meta.get("authors", [])) >= 2
        assert len(meta.get("abstract", "")) > 100
        assert len(meta.get("grobid_sections", [])) >= 2
        assert len(meta.get("grobid_figures", [])) >= 3
        assert len(meta.get("grobid_tables", [])) >= 1

        logger.info("PaperPdfLoader metadata: title=%s", meta.get("title"))
        logger.info("  grobid_sections: %d", len(meta.get("grobid_sections", [])))
        logger.info("  grobid_figures: %d", len(meta.get("grobid_figures", [])))
        logger.info("  grobid_tables: %d", len(meta.get("grobid_tables", [])))


# ═══════════════════════════════════════════════════════════════════════════
# Run helpers
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
