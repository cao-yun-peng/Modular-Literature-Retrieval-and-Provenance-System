"""Unit tests for PaperPdfLoader GROBID integration and paper metadata extraction.

Tests verify:
- GROBID metadata extraction via mocked GrobidClient/GrobidTEIParser
- Graceful fallback to regex when GROBID is unavailable
- _grobid_to_metadata conversion produces compatible metadata dict
- Supplement of regex-extracted fields (keywords, references, equations, etc.)
"""

from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from src.core.types import Document
from src.libs.loader.pdf_loader import PaperPdfLoader


# =============================================================================
# Helpers: build a mock Paper object matching grobid_parser.Paper
# =============================================================================

def _mock_paper(**overrides):
    """Create a mock Paper with sensible defaults for testing."""
    from src.libs.loader.grobid_parser import Section, FigureOrTable

    defaults = {
        "title": "Test Paper Title",
        "authors": ["Alice Smith", "Bob Johnson"],
        "abstract": "This is a test abstract about machine learning.",
        "sections": [
            Section(heading="Introduction", paragraphs=["Intro paragraph 1.", "Intro paragraph 2."], level=1),
            Section(heading="Methods", paragraphs=["Methods paragraph."], level=1),
        ],
        "figures": [
            FigureOrTable(id="fig_0", type="figure", caption="Figure 1: Overview", description="Architecture overview."),
        ],
        "tables": [
            FigureOrTable(id="tab_0", type="table", caption="Table 1: Results", description="A\tB\n1\t2"),
        ],
        "doi": "10.1234/test.2025",
        "arxiv_id": None,
    }
    defaults.update(overrides)
    paper = Mock()
    for k, v in defaults.items():
        setattr(paper, k, v)
    return paper


# =============================================================================
# _grobid_to_metadata conversion tests
# =============================================================================

class TestGrobidToMetadata:
    """Tests for PaperPdfLoader._grobid_to_metadata()."""

    @pytest.fixture
    def loader(self):
        return PaperPdfLoader(use_grobid=False)

    def test_converts_title(self, loader):
        paper = _mock_paper(title="My Paper")
        meta = loader._grobid_to_metadata(paper)
        assert meta["title"] == "My Paper"

    def test_converts_authors(self, loader):
        paper = _mock_paper(authors=["Alice Smith"])
        meta = loader._grobid_to_metadata(paper)
        assert meta["authors"] == ["Alice Smith"]

    def test_converts_abstract(self, loader):
        paper = _mock_paper(abstract="Test abstract.")
        meta = loader._grobid_to_metadata(paper)
        assert meta["abstract"] == "Test abstract."

    def test_converts_doi(self, loader):
        paper = _mock_paper(doi="10.1234/x")
        meta = loader._grobid_to_metadata(paper)
        assert meta["doi"] == "10.1234/x"

    def test_converts_arxiv_id(self, loader):
        paper = _mock_paper(arxiv_id="2101.12345")
        meta = loader._grobid_to_metadata(paper)
        assert meta["arxiv_id"] == "2101.12345"

    def test_converts_sections_to_compatible_format(self, loader):
        paper = _mock_paper()
        meta = loader._grobid_to_metadata(paper)
        assert "sections" in meta
        assert isinstance(meta["sections"], list)
        assert meta["sections"][0]["title"] == "Introduction"
        assert meta["sections"][0]["level"] == 1

    def test_converts_toc(self, loader):
        paper = _mock_paper()
        meta = loader._grobid_to_metadata(paper)
        assert "toc" in meta
        assert meta["toc"] == ["Introduction", "Methods"]

    def test_includes_grobid_sections(self, loader):
        paper = _mock_paper()
        meta = loader._grobid_to_metadata(paper)
        assert "grobid_sections" in meta
        gs = meta["grobid_sections"]
        assert len(gs) == 2
        assert gs[0]["heading"] == "Introduction"
        assert "paragraphs" in gs[0]
        assert gs[0]["level"] == 1

    def test_includes_grobid_figures(self, loader):
        paper = _mock_paper()
        meta = loader._grobid_to_metadata(paper)
        assert "grobid_figures" in meta
        assert len(meta["grobid_figures"]) == 1
        assert meta["grobid_figures"][0]["id"] == "fig_0"

    def test_includes_grobid_tables(self, loader):
        paper = _mock_paper()
        meta = loader._grobid_to_metadata(paper)
        assert "grobid_tables" in meta
        assert len(meta["grobid_tables"]) == 1
        assert meta["grobid_tables"][0]["id"] == "tab_0"

    def test_empty_fields_omitted(self, loader):
        paper = _mock_paper(title="", authors=[], abstract="", doi=None, arxiv_id=None,
                            sections=[], figures=[], tables=[])
        meta = loader._grobid_to_metadata(paper)
        assert "title" not in meta
        assert "authors" not in meta
        assert "abstract" not in meta
        assert "doi" not in meta
        assert "sections" not in meta

    def test_sections_without_headings_excluded_from_toc(self, loader):
        """Sections with empty heading should not appear in toc."""
        from src.libs.loader.grobid_parser import Section
        paper = _mock_paper(sections=[
            Section(heading="Intro", paragraphs=["p1"], level=1),
            Section(heading="", paragraphs=["p2"], level=1),
        ])
        meta = loader._grobid_to_metadata(paper)
        assert meta["toc"] == ["Intro"]


# =============================================================================
# load() integration tests with mocked GROBID
# =============================================================================

class TestPaperPdfLoaderLoadWithGrobid:
    """Tests for PaperPdfLoader.load() with mocked GROBID path."""

    @pytest.fixture
    def tmp_pdf(self, tmp_path):
        """Create a minimal PDF file for testing."""
        pdf_path = tmp_path / "test.pdf"
        # Minimal valid PDF bytes
        pdf_path.write_bytes(
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000058 00000 n \n0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
        )
        return pdf_path

    def test_load_with_grobid_success(self, tmp_pdf, monkeypatch):
        """GROBID succeeds → _grobid_to_metadata is used."""
        loader = PaperPdfLoader(use_grobid=True, grobid_url="http://localhost:8070")

        # Mock GrobidClient
        mock_client = MagicMock()
        mock_client.is_alive.return_value = True
        mock_client.pdf_to_tei.return_value = "<TEI xmlns='http://www.tei-c.org/ns/1.0'/>"

        # Mock GrobidTEIParser
        mock_parser = MagicMock()
        mock_parser.parse.return_value = _mock_paper()

        # Patch the imports inside load()
        def mock_import_grobid():
            class FakeGrobidClient:
                def __init__(self, base_url=None, timeout=None):
                    pass
                def is_alive(self):
                    return True
                def pdf_to_tei(self, pdf_path, **kwargs):
                    return "<TEI/>"

            class FakeGrobidParser:
                def __init__(self, tei_xml):
                    pass
                def parse(self):
                    return _mock_paper()

            return FakeGrobidClient, FakeGrobidParser

        # Monkey-patch the load method's internal import
        import src.libs.loader.pdf_loader as pdf_loader_mod
        original_load = loader.load

        def patched_load(file_path):
            # We need to inject our fake GrobidClient/Parser
            # Override the internal lazy import
            import src.libs.loader.grobid_parser as gp
            orig_client = gp.GrobidClient
            orig_parser = gp.GrobidTEIParser
            gp.GrobidClient = lambda base_url=None, timeout=None: mock_client
            gp.GrobidTEIParser = lambda tei_xml: mock_parser
            try:
                return original_load(file_path)
            finally:
                gp.GrobidClient = orig_client
                gp.GrobidTEIParser = orig_parser

        monkeypatch.setattr(loader, "load", patched_load)

        doc = loader.load(tmp_pdf)
        assert doc.metadata["doc_type"] == "paper"
        assert doc.metadata["paper_mode"] is True

    def test_load_grobid_unavailable_falls_back(self, tmp_pdf):
        """GROBID unavailable → regex fallback used."""
        loader = PaperPdfLoader(use_grobid=True, grobid_url="http://127.0.0.1:19999")

        doc = loader.load(tmp_pdf)
        assert doc.metadata["doc_type"] == "paper"
        assert doc.metadata["paper_mode"] is True
        # Should still produce a valid document (regex fallback worked)

    def test_load_grobid_disabled_uses_regex(self, tmp_pdf):
        """use_grobid=False → regex path directly."""
        loader = PaperPdfLoader(use_grobid=False)

        doc = loader.load(tmp_pdf)
        assert doc.metadata["doc_type"] == "paper"
        assert doc.metadata["paper_mode"] is True


# =============================================================================
# Regex fallback method tests
# =============================================================================

class TestPaperPdfLoaderRegexFallback:
    """Tests that regex-based metadata extraction still works."""

    @pytest.fixture
    def loader(self):
        return PaperPdfLoader(use_grobid=False)

    def test_extract_title_from_markdown(self, loader):
        text = "# Deep Learning Paper\n\nAbstract content here..."
        title = loader._extract_title(text)
        assert title == "Deep Learning Paper"

    def test_extract_authors_from_line_after_title(self, loader):
        text = "Deep Learning Paper\nAlice Smith, Bob Johnson\n\nAbstract..."
        authors = loader._extract_authors(text, "Deep Learning Paper")
        assert "Alice Smith" in authors
        assert "Bob Johnson" in authors

    def test_extract_keywords_from_text(self, loader):
        text = "Keywords: deep learning, NLP, transformers\n\nIntroduction..."
        keywords = loader._extract_keywords(text)
        assert "deep learning" in keywords
        assert "NLP" in keywords

    def test_extract_doi_from_text(self, loader):
        text = "DOI: 10.1234/example.paper\n\nMore text..."
        doi = loader._extract_doi(text)
        assert doi == "10.1234/example.paper"

    def test_extract_arxiv_id_from_text(self, loader):
        text = "arXiv: 2101.12345v2\n\nMore text..."
        arxiv_id = loader._extract_arxiv_id(text)
        assert arxiv_id == "2101.12345"

    def test_extract_equations_from_text(self, loader):
        text = "The loss function is $$L = \\sum_i (y_i - \\hat{y}_i)^2$$"
        equations = loader._extract_equations(text)
        assert len(equations) >= 1
        assert any("L =" in eq for eq in equations)

    def test_extract_sections_from_markdown(self, loader):
        text = "# Title\n\n## Introduction\nIntro text.\n\n## Methods\nMethods text."
        sections = loader._extract_sections(text)
        assert len(sections) == 3  # Title, Introduction, Methods
        assert sections[1]["title"] == "Introduction"
        assert sections[1]["level"] == 2

    def test_extract_venue_year(self, loader):
        text = "2025 Conference on Machine Learning\n\n"
        result = loader._extract_venue_year(text)
        assert result["year"] == 2025


# =============================================================================
# Constructor and configuration tests
# =============================================================================

class TestPaperPdfLoaderConfig:
    """Tests for PaperPdfLoader initialization and configuration."""

    def test_default_grobid_enabled(self):
        loader = PaperPdfLoader()
        assert loader.use_grobid is True
        assert loader.grobid_url == "http://localhost:8070"

    def test_grobid_disabled(self):
        loader = PaperPdfLoader(use_grobid=False)
        assert loader.use_grobid is False

    def test_custom_grobid_url(self):
        loader = PaperPdfLoader(grobid_url="http://grobid:8080")
        assert loader.grobid_url == "http://grobid:8080"

    def test_inherits_pdf_loader_config(self):
        loader = PaperPdfLoader(extract_images=False, image_storage_dir="custom/images")
        assert loader.extract_images is False
        assert loader.image_storage_dir == Path("custom/images")

    def test_table_config(self):
        loader = PaperPdfLoader(table_storage_dir="custom/tables", extract_tables=False)
        assert loader.table_storage_dir == Path("custom/tables")
        assert loader.extract_tables is False
