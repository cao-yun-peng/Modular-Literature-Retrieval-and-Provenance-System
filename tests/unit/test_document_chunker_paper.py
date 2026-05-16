"""Unit tests for GROBID-aware paper chunking in DocumentChunker.

Tests the full paper chunking pipeline when ``grobid_sections`` are present:

1. Title + Abstract → one chunk (split into two if >1000 chars)
2. Figures → per-figure chunks with ``chunk_type: "figure"``
3. Tables → per-table chunks with ``chunk_type: "table"``
4. Body sections → split by splitter, with ``[FIG_REF: ...]`` / ``[TABLE_REF: ...]``
   placeholders and ``linked_figures`` / ``linked_tables`` in metadata

Also verifies the legacy path is unchanged when no GROBID data exists.
"""

import pytest
from unittest.mock import Mock

from src.core.types import Document, Chunk
from src.core.settings import Settings
from src.ingestion.chunking import DocumentChunker
from src.libs.splitter.base_splitter import BaseSplitter


# =============================================================================
# Fake splitter — paragraph-based (same as existing chunker tests)
# =============================================================================

class FakeSplitter(BaseSplitter):
    """Splits on double newlines — deterministic and predictable."""

    def __init__(self, chunk_size=100, overlap=0, **kwargs):
        pass

    def split_text(self, text: str) -> list[str]:
        paragraphs = text.split("\n\n")
        return [p.strip() for p in paragraphs if p.strip()]


@pytest.fixture
def fake_settings():
    settings = Mock(spec=Settings)
    settings.splitter = Mock()
    settings.splitter.provider = "fake"
    settings.splitter.chunk_size = 100
    settings.splitter.overlap = 0
    return settings


@pytest.fixture
def chunker(fake_settings, monkeypatch):
    from src.libs.splitter import splitter_factory
    original = splitter_factory.SplitterFactory.create

    def mock_create(settings):
        return FakeSplitter()

    monkeypatch.setattr(splitter_factory.SplitterFactory, "create", mock_create)
    return DocumentChunker(fake_settings)


# =============================================================================
# Helper: build a paper Document with GROBID metadata
# =============================================================================

def make_paper_document(**overrides) -> Document:
    """Build a Document with GROBID paper metadata for testing."""
    defaults = {
        "id": "doc_paper_001",
        "text": "# Test Paper\n\n## Abstract\nTest abstract.\n\n## Introduction\nBody text referencing Figure 1.\n\n## Methods\nMethods text referencing Table 1.",
        "metadata": {
            "source_path": "papers/test.pdf",
            "doc_type": "paper",
            "paper_mode": True,
            "title": "Test Paper",
            "abstract": "Test abstract.",
            "authors": ["Alice Smith"],
            "grobid_sections": [
                {"heading": "Introduction", "paragraphs": ["Body text referencing Figure 1."], "level": 1},
                {"heading": "Methods", "paragraphs": ["Methods text referencing Table 1."], "level": 1},
            ],
            "grobid_figures": [
                {"id": "fig_0", "type": "figure", "caption": "Figure 1: Overview", "description": "Architecture diagram."},
                {"id": "fig_1", "type": "figure", "caption": "Figure 2: Detail", "description": "Detail view."},
            ],
            "grobid_tables": [
                {"id": "tab_0", "type": "table", "caption": "Table 1: Results", "description": "A\tB\n1\t2"},
            ],
        },
    }
    defaults["metadata"].update(overrides.pop("metadata_extra", {}))
    defaults.update(overrides)
    return Document(id=defaults["id"], text=defaults["text"], metadata=defaults["metadata"])


# =============================================================================
# Title + Abstract chunking
# =============================================================================

class TestTitleAbstractChunking:
    """Title + Abstract → one chunk (two if >1000 chars)."""

    def test_title_and_abstract_combined_as_one_chunk(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        ta_chunks = [c for c in chunks if c.metadata.get("chunk_type") in ("title_abstract", "title", "abstract")]
        assert len(ta_chunks) == 1
        assert ta_chunks[0].metadata["chunk_type"] == "title_abstract"
        assert "# Test Paper" in ta_chunks[0].text
        assert "Test abstract" in ta_chunks[0].text

    def test_title_abstract_splits_when_over_1000_chars(self, chunker):
        long_abstract = "Long abstract content. " * 100  # ~2500 chars with spaces
        doc = make_paper_document(metadata_extra={"abstract": long_abstract})
        chunks = chunker.split_document(doc)

        ta_chunks = [c for c in chunks if c.metadata.get("chunk_type") in ("title_abstract", "title", "abstract")]
        assert len(ta_chunks) == 2
        types = {c.metadata["chunk_type"] for c in ta_chunks}
        assert types == {"title", "abstract"}

    def test_only_title_no_abstract(self, chunker):
        doc = make_paper_document(metadata_extra={"abstract": ""})
        chunks = chunker.split_document(doc)

        ta_chunks = [c for c in chunks if c.metadata.get("chunk_type") in ("title_abstract", "title", "abstract")]
        assert len(ta_chunks) == 1
        assert "# Test Paper" in ta_chunks[0].text

    def test_title_abstract_skipped_when_both_empty(self, chunker):
        doc = make_paper_document(metadata_extra={"title": "", "abstract": ""})
        chunks = chunker.split_document(doc)

        ta_chunks = [c for c in chunks if c.metadata.get("chunk_type") in ("title_abstract", "title", "abstract")]
        assert len(ta_chunks) == 0


# =============================================================================
# Figure chunk creation
# =============================================================================

class TestFigureChunking:
    """Each GROBID figure → one chunk with chunk_type: figure."""

    def test_figures_created_as_separate_chunks(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        fig_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "figure"]
        assert len(fig_chunks) == 2

    def test_figure_chunk_contains_placeholder(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        fig_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "figure"]
        assert "[FIGURE: fig_0]" in fig_chunks[0].text
        assert "[FIGURE: fig_1]" in fig_chunks[1].text

    def test_figure_chunk_contains_caption(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        fig_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "figure"]
        assert "Figure 1: Overview" in fig_chunks[0].text
        assert "Architecture diagram" in fig_chunks[0].text

    def test_figure_chunk_metadata_has_figure_id(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        fig_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "figure"]
        assert fig_chunks[0].metadata["figure_id"] == "fig_0"
        assert fig_chunks[0].metadata["figure_caption"] == "Figure 1: Overview"

    def test_no_figures_when_grobid_figures_empty(self, chunker):
        doc = make_paper_document(metadata_extra={"grobid_figures": []})
        chunks = chunker.split_document(doc)

        fig_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "figure"]
        assert len(fig_chunks) == 0


# =============================================================================
# Table chunk creation
# =============================================================================

class TestTableChunking:
    """Each GROBID table → one chunk with chunk_type: table."""

    def test_tables_created_as_separate_chunks(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        tab_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert len(tab_chunks) == 1

    def test_table_chunk_contains_placeholder(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        tab_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert "[TABLE_DATA: tab_0]" in tab_chunks[0].text

    def test_table_chunk_contains_content(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        tab_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert "Table 1: Results" in tab_chunks[0].text
        assert "A\tB" in tab_chunks[0].text

    def test_table_chunk_metadata_has_table_id(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        tab_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert tab_chunks[0].metadata["table_id"] == "tab_0"
        assert tab_chunks[0].metadata["table_caption"] == "Table 1: Results"

    def test_no_tables_when_grobid_tables_empty(self, chunker):
        doc = make_paper_document(metadata_extra={"grobid_tables": []})
        chunks = chunker.split_document(doc)

        tab_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "table"]
        assert len(tab_chunks) == 0


# =============================================================================
# Body section chunking with figure/table references
# =============================================================================

class TestBodySectionChunking:
    """Body sections → split by splitter, with reference placeholders and linkage."""

    def test_body_chunks_have_section_metadata(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        body_chunks = [c for c in chunks if c.metadata.get("section")]
        assert len(body_chunks) >= 1
        sections = {c.metadata["section"] for c in body_chunks}
        assert "Introduction" in sections
        assert "Methods" in sections

    def test_figure_references_replaced_with_placeholder(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        intro_chunks = [c for c in chunks if c.metadata.get("section") == "Introduction"]
        assert len(intro_chunks) > 0
        # "Figure 1" should be replaced with "[FIG_REF: fig_0]"
        intro_text = " ".join(c.text for c in intro_chunks)
        assert "[FIG_REF: fig_0]" in intro_text

    def test_table_references_replaced_with_placeholder(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        methods_chunks = [c for c in chunks if c.metadata.get("section") == "Methods"]
        assert len(methods_chunks) > 0
        methods_text = " ".join(c.text for c in methods_chunks)
        assert "[TABLE_REF: tab_0]" in methods_text

    def test_body_chunk_metadata_has_linked_figures(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        intro_chunks = [c for c in chunks if c.metadata.get("section") == "Introduction"]
        assert len(intro_chunks) > 0
        # At least one fragment should have linked_figures
        linked = []
        for c in intro_chunks:
            linked.extend(c.metadata.get("linked_figures", []))
        assert "fig_0" in linked

    def test_body_chunk_metadata_has_linked_tables(self, chunker):
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        methods_chunks = [c for c in chunks if c.metadata.get("section") == "Methods"]
        assert len(methods_chunks) > 0
        linked = []
        for c in methods_chunks:
            linked.extend(c.metadata.get("linked_tables", []))
        assert "tab_0" in linked

    def test_no_linked_assets_when_none_referenced(self, chunker):
        doc = make_paper_document(metadata_extra={
            "grobid_sections": [
                {"heading": "Introduction", "paragraphs": ["Just text, no figures or tables."], "level": 1},
            ],
        })
        chunks = chunker.split_document(doc)

        body_chunks = [c for c in chunks if c.metadata.get("section")]
        assert len(body_chunks) > 0
        assert "linked_figures" not in body_chunks[0].metadata
        assert "linked_tables" not in body_chunks[0].metadata

    def test_chunk_order_is_title_abstract_figures_tables_body(self, chunker):
        """Chunks should appear in order: TA → figures → tables → body."""
        doc = make_paper_document()
        chunks = chunker.split_document(doc)

        types_in_order = []
        for c in chunks:
            ct = c.metadata.get("chunk_type", "")
            if ct:
                types_in_order.append(ct)
            elif c.metadata.get("section"):
                types_in_order.append("body")

        # Title/abstract first
        assert types_in_order[0] == "title_abstract"
        # Figures next
        fig_indices = [i for i, t in enumerate(types_in_order) if t == "figure"]
        tab_indices = [i for i, t in enumerate(types_in_order) if t == "table"]
        body_indices = [i for i, t in enumerate(types_in_order) if t == "body"]
        # All figures before body
        if fig_indices and body_indices:
            assert max(fig_indices) < min(body_indices)
        # All tables before body
        if tab_indices and body_indices:
            assert max(tab_indices) < min(body_indices)

    # -- Figure/Table reference mapping edge cases --

    def test_fig_reference_case_insensitive(self, chunker):
        """'fig. 1', 'Fig. 1', 'Figure 1' all map to the same figure."""
        doc = make_paper_document(metadata_extra={
            "grobid_sections": [
                {"heading": "Intro", "paragraphs": ["See fig. 1 for details."], "level": 1},
            ],
        })
        chunks = chunker.split_document(doc)
        body = [c for c in chunks if c.metadata.get("section") == "Intro"]
        combined = " ".join(c.text for c in body)
        assert "[FIG_REF: fig_0]" in combined

    def test_multiple_same_reference_deduplicated(self, chunker):
        """Multiple references to Figure 1 produce one linked_figure entry per section."""
        doc = make_paper_document(metadata_extra={
            "grobid_sections": [
                {"heading": "Intro", "paragraphs": ["See Figure 1. Also Figure 1 shows details."], "level": 1},
            ],
        })
        chunks = chunker.split_document(doc)
        body = [c for c in chunks if c.metadata.get("section") == "Intro"]
        # Each fragment carries the same linked_figures list — check unique set
        all_linked = []
        for c in body:
            all_linked.extend(c.metadata.get("linked_figures", []))
        # References within one section are deduplicated (even if text has "Figure 1" twice)
        assert set(all_linked) == {"fig_0"}

    def test_reference_to_nonexistent_figure_not_linked(self, chunker):
        """'Figure 99' should not create a link if no such figure exists."""
        doc = make_paper_document(metadata_extra={
            "grobid_sections": [
                {"heading": "Intro", "paragraphs": ["See Figure 99 for unrelated work."], "level": 1},
            ],
        })
        chunks = chunker.split_document(doc)
        body = [c for c in chunks if c.metadata.get("section") == "Intro"]
        all_linked = []
        for c in body:
            all_linked.extend(c.metadata.get("linked_figures", []))
        assert all_linked == []
        # Text unchanged for unmatched references (not replaced)
        combined = " ".join(c.text for c in body)
        assert "Figure 99" in combined


# =============================================================================
# Asset reference map building
# =============================================================================

class TestAssetRefMap:
    """Tests for _build_asset_ref_map — maps paper numbers to GROBID IDs."""

    @pytest.fixture
    def chunker(self, fake_settings, monkeypatch):
        from src.libs.splitter import splitter_factory

        def mock_create(settings):
            return FakeSplitter()
        monkeypatch.setattr(splitter_factory.SplitterFactory, "create", mock_create)
        return DocumentChunker(fake_settings)

    def test_sequential_mapping(self, chunker):
        """First asset → paper number 1, second → 2, etc."""
        assets = [
            {"id": "fig_0", "caption": "Fig 1"},
            {"id": "fig_1", "caption": "Fig 2"},
        ]
        ref_map = chunker._build_asset_ref_map(assets)
        assert ref_map[1] == "fig_0"
        assert ref_map[2] == "fig_1"

    def test_sequential_with_xml_ids(self, chunker):
        """Sequential mapping ignores the numeric suffix in xml:id."""
        assets = [
            {"id": "fig3", "caption": "Fig 3"},
            {"id": "fig5", "caption": "Fig 5"},
        ]
        ref_map = chunker._build_asset_ref_map(assets)
        assert ref_map[1] == "fig3"
        assert ref_map[2] == "fig5"

    def test_non_numeric_ids(self, chunker):
        """When no number in ID, sequential still works."""
        assets = [
            {"id": "fig_overview", "caption": "Fig 1"},
            {"id": "fig_detail", "caption": "Fig 2"},
        ]
        ref_map = chunker._build_asset_ref_map(assets)
        assert ref_map[1] == "fig_overview"
        assert ref_map[2] == "fig_detail"

    def test_empty_assets(self, chunker):
        assert chunker._build_asset_ref_map([]) == {}


# =============================================================================
# Legacy path — backward compatibility
# =============================================================================

class TestLegacyPath:
    """When no grobid_sections, the old behavior is preserved."""

    @pytest.fixture
    def chunker(self, fake_settings, monkeypatch):
        from src.libs.splitter import splitter_factory

        def mock_create(settings):
            return FakeSplitter()
        monkeypatch.setattr(splitter_factory.SplitterFactory, "create", mock_create)
        return DocumentChunker(fake_settings)

    def test_legacy_split_uses_markdown_headings(self, chunker):
        doc = Document(
            id="doc_legacy",
            text="# Title\n\n## Introduction\nIntro text.\n\n## Methods\nMethods text.",
            metadata={
                "source_path": "papers/legacy.pdf",
                "doc_type": "paper",
                "paper_mode": True,
            },
        )
        chunks = chunker.split_document(doc)
        # Should produce chunks (no grobid_sections → legacy path)
        assert len(chunks) > 0
        # Each chunk should have standard metadata
        for c in chunks:
            assert "chunk_index" in c.metadata
            assert "source_ref" in c.metadata

    def test_legacy_path_no_chunk_type_field(self, chunker):
        doc = Document(
            id="doc_legacy2",
            text="# Title\n\n## Intro\nIntro text.",
            metadata={"source_path": "papers/old.pdf", "doc_type": "paper", "paper_mode": True},
        )
        chunks = chunker.split_document(doc)
        # Legacy chunks should NOT have chunk_type (no GROBID-specific processing)
        for c in chunks:
            assert "chunk_type" not in c.metadata

    def test_legacy_references_section_gets_reference_chunk_type(self, monkeypatch):
        """Legacy path: chunks from # References section must have chunk_type='reference'."""
        from unittest.mock import Mock
        from src.libs.splitter import splitter_factory
        from src.ingestion.chunking.document_chunker import DocumentChunker
        from src.core.types import Document

        settings = Mock()
        settings.splitter = Mock()
        settings.splitter.provider = "fake"
        settings.splitter.chunk_size = 100
        settings.splitter.overlap = 0

        def mock_create(settings):
            return FakeSplitter()
        monkeypatch.setattr(splitter_factory.SplitterFactory, "create", mock_create)

        chunker = DocumentChunker(settings)
        text = (
            "# Introduction\n\nThis is the introduction text with some content.\n\n"
            "# Methods\n\nWe used standard methods for the experiment.\n\n"
            "# References\n\n[1] Smith, J. et al. A great paper. Nature, 2020.\n"
            "[2] Doe, A. et al. Another paper. Science, 2021.\n"
        )
        doc = Document(
            id="test_doc",
            text=text,
            metadata={"source_path": "papers/test.pdf", "doc_type": "paper", "paper_mode": True}
        )
        chunks = chunker.split_document(doc)

        ref_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "reference"]
        assert len(ref_chunks) >= 1, "References section should produce reference-type chunks"
        assert all("Smith" in c.text or "Doe" in c.text or "[1]" in c.text or "[2]" in c.text
                   for c in ref_chunks)


# =============================================================================
# Reference chunk tests
# =============================================================================

class TestReferenceChunking:
    """reference_raw in GROBID → chunks with chunk_type: reference."""

    def test_grobid_references_raw_produces_reference_chunks(self, monkeypatch):
        """GROBID path: references_raw in metadata must produce chunk_type='reference'."""
        from unittest.mock import Mock
        from src.libs.splitter import splitter_factory
        from src.ingestion.chunking.document_chunker import DocumentChunker
        from src.core.types import Document

        settings = Mock()
        settings.splitter = Mock()
        settings.splitter.provider = "fake"
        settings.splitter.chunk_size = 100
        settings.splitter.overlap = 0

        def mock_create(settings):
            return FakeSplitter()
        monkeypatch.setattr(splitter_factory.SplitterFactory, "create", mock_create)

        chunker = DocumentChunker(settings)
        ref_text = "[1] Smith, J. et al. Nature, 2020.\n[2] Doe, A. et al. Science, 2021.\n"
        doc = Document(
            id="test_doc",
            text="# Abstract\n\nSome abstract.\n\n# Introduction\n\nBody text here.",
            metadata={
                "source_path": "papers/test.pdf",
                "doc_type": "paper",
                "paper_mode": True,
                "grobid_sections": [
                    {"heading": "Abstract", "paragraphs": ["Some abstract."], "level": 1},
                    {"heading": "Introduction", "paragraphs": ["Body text here."], "level": 1},
                ],
                "references_raw": ref_text,
            }
        )
        chunks = chunker.split_document(doc)

        ref_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "reference"]
        assert len(ref_chunks) >= 1, "references_raw should produce reference-type chunks"
        assert any("Smith" in c.text for c in ref_chunks)

    def test_legacy_chinese_references_heading(self, monkeypatch):
        """Legacy path: # 参考文献 should also produce reference-type chunks."""
        from unittest.mock import Mock
        from src.libs.splitter import splitter_factory
        from src.ingestion.chunking.document_chunker import DocumentChunker
        from src.core.types import Document

        settings = Mock()
        settings.splitter = Mock()
        settings.splitter.provider = "fake"
        settings.splitter.chunk_size = 100
        settings.splitter.overlap = 0

        def mock_create(settings):
            return FakeSplitter()
        monkeypatch.setattr(splitter_factory.SplitterFactory, "create", mock_create)

        chunker = DocumentChunker(settings)
        text = (
            "# 引言\n\n这是引言内容。\n\n"
            "# 参考文献\n\n[1] 张三等. 深度学习综述. 计算机学报, 2023.\n"
        )
        doc = Document(
            id="test_doc",
            text=text,
            metadata={"source_path": "papers/test.pdf", "doc_type": "paper", "paper_mode": True}
        )
        chunks = chunker.split_document(doc)

        ref_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "reference"]
        assert len(ref_chunks) >= 1, "Chinese 参考文献 section should produce reference-type chunks"
