"""Unit tests for GrobidTEIParser and data classes.

Validates TEI XML parsing for structured academic paper metadata:
- Title, authors, abstract extraction
- Section parsing with heading levels
- Figure and table extraction
- DOI and arXiv identifier extraction
- Edge cases: empty XML, missing elements, malformed input
"""

import pytest
from src.libs.loader.grobid_parser import (
    GrobidTEIParser,
    GrobidClient,
    Paper,
    Section,
    FigureOrTable,
)


# =============================================================================
# Sample TEI XML fixtures
# =============================================================================

SAMPLE_TEI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a">Deep Learning for Natural Language Processing</title>
      </titleStmt>
      <sourceDesc>
        <biblStruct>
          <author>
            <persName>
              <forename>Alice</forename>
              <surname>Smith</surname>
            </persName>
          </author>
          <author>
            <persName>
              <forename>Bob</forename>
              <surname>Johnson</surname>
            </persName>
          </author>
          <idno type="DOI">10.1234/example.2025</idno>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract>
        <p>This paper presents a novel approach to deep learning applied to
        natural language processing tasks. We demonstrate state-of-the-art
        results on benchmark datasets.</p>
      </abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head>1. Introduction</head>
        <p>Deep learning has revolutionized NLP in recent years.</p>
        <p>Transformer architectures have become the standard approach.</p>
      </div>
      <div>
        <head>2. Related Work</head>
        <p>Previous work in this area includes BERT and GPT models.</p>
        <div>
          <head>2.1 Subsection</head>
          <p>This is a subsection with more detail.</p>
        </div>
      </div>
      <div>
        <head>3. Methodology</head>
        <p>We propose a new architecture combining attention mechanisms.</p>
        <p>Figure 1 illustrates the overall architecture.</p>
        <figure xml:id="fig1">
          <head>Figure 1: System Architecture</head>
          <figDesc>The architecture consists of encoder and decoder layers.</figDesc>
        </figure>
        <p>Table 1 shows the hyperparameter settings.</p>
        <table xml:id="tab1">
          <head>Table 1: Hyperparameters</head>
          <row><cell>Learning Rate</cell><cell>0.001</cell></row>
          <row><cell>Batch Size</cell><cell>32</cell></row>
        </table>
      </div>
    </body>
  </text>
</TEI>"""

MINIMAL_TEI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>Minimal Paper</title>
      </titleStmt>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div><p>Just one paragraph.</p></div>
    </body>
  </text>
</TEI>"""


# =============================================================================
# Data class tests
# =============================================================================

class TestPaperDataclass:
    """Tests for Paper, Section, FigureOrTable dataclasses."""

    def test_paper_default_values(self):
        paper = Paper()
        assert paper.title == ""
        assert paper.authors == []
        assert paper.abstract == ""
        assert paper.sections == []
        assert paper.figures == []
        assert paper.tables == []

    def test_paper_to_dict_serialization(self):
        paper = Paper(
            title="Test Title",
            authors=["Alice Smith", "Bob Johnson"],
            abstract="Test abstract.",
            sections=[Section(heading="Intro", paragraphs=["p1"], level=1)],
            figures=[FigureOrTable(id="fig_0", type="figure", caption="Fig 1", description="desc")],
            tables=[],
            doi="10.1234/test",
        )
        d = paper.to_dict()
        assert d["title"] == "Test Title"
        assert len(d["authors"]) == 2
        assert d["abstract"] == "Test abstract."
        assert len(d["sections"]) == 1
        assert d["sections"][0]["heading"] == "Intro"
        assert d["doi"] == "10.1234/test"

    def test_section_defaults(self):
        s = Section(heading="Test")
        assert s.heading == "Test"
        assert s.paragraphs == []
        assert s.level == 1

    def test_figure_or_table_defaults(self):
        ft = FigureOrTable(id="f1", type="figure", caption="c", description="d")
        assert ft.id == "f1"
        assert ft.type == "figure"
        assert ft.caption == "c"
        assert ft.description == "d"


# =============================================================================
# GrobidTEIParser tests
# =============================================================================

class TestGrobidTEIParser:
    """Tests for GrobidTEIParser."""

    @pytest.fixture
    def parser(self):
        return GrobidTEIParser(SAMPLE_TEI_XML)

    @pytest.fixture
    def minimal_parser(self):
        return GrobidTEIParser(MINIMAL_TEI_XML)

    # -- Parse result structure --

    def test_parse_returns_paper(self, parser):
        paper = parser.parse()
        assert isinstance(paper, Paper)

    # -- Title --

    def test_extract_title(self, parser):
        paper = parser.parse()
        assert paper.title == "Deep Learning for Natural Language Processing"

    def test_extract_title_minimal(self, minimal_parser):
        paper = minimal_parser.parse()
        assert paper.title == "Minimal Paper"

    # -- Authors --

    def test_extract_authors(self, parser):
        paper = parser.parse()
        assert len(paper.authors) == 2
        assert paper.authors[0] == "Alice Smith"
        assert paper.authors[1] == "Bob Johnson"

    def test_extract_authors_empty(self, minimal_parser):
        paper = minimal_parser.parse()
        assert paper.authors == []

    # -- Abstract --

    def test_extract_abstract(self, parser):
        paper = parser.parse()
        assert "novel approach" in paper.abstract
        assert "state-of-the-art" in paper.abstract

    def test_extract_abstract_missing(self, minimal_parser):
        paper = minimal_parser.parse()
        assert paper.abstract == ""

    # -- Sections --

    def test_extract_sections_count(self, parser):
        paper = parser.parse()
        # 3 top-level divs + 1 sub-div = 4 sections (with level tracking)
        assert len(paper.sections) == 4

    def test_extract_sections_headings(self, parser):
        paper = parser.parse()
        headings = [s.heading for s in paper.sections]
        assert "Introduction" in headings
        assert "Related Work" in headings
        assert "Subsection" in headings
        assert "Methodology" in headings

    def test_extract_sections_levels(self, parser):
        paper = parser.parse()
        # Introduction (level 1) and Subsection (level 2)
        intro = next(s for s in paper.sections if s.heading == "Introduction")
        assert intro.level == 1
        sub = next(s for s in paper.sections if s.heading == "Subsection")
        assert sub.level == 2

    def test_extract_sections_paragraphs(self, parser):
        paper = parser.parse()
        intro = next(s for s in paper.sections if s.heading == "Introduction")
        assert len(intro.paragraphs) == 2
        assert "revolutionized NLP" in intro.paragraphs[0]

    def test_extract_sections_heading_number_stripped(self, parser):
        """Section headings should have their number prefix removed."""
        paper = parser.parse()
        intro = next(s for s in paper.sections if "Introduction" in s.heading)
        assert not intro.heading.startswith("1.")

    def test_extract_sections_minimal(self, minimal_parser):
        paper = minimal_parser.parse()
        assert len(paper.sections) == 1
        assert paper.sections[0].paragraphs == ["Just one paragraph."]

    # -- Figures --

    def test_extract_figures(self, parser):
        paper = parser.parse()
        assert len(paper.figures) == 1
        fig = paper.figures[0]
        assert fig.type == "figure"
        assert "System Architecture" in fig.caption
        assert "encoder and decoder" in fig.description

    def test_extract_figures_minimal(self, minimal_parser):
        paper = minimal_parser.parse()
        assert paper.figures == []

    # -- Tables --

    def test_extract_tables(self, parser):
        paper = parser.parse()
        assert len(paper.tables) == 1
        tab = paper.tables[0]
        assert tab.type == "table"
        assert "Hyperparameters" in tab.caption
        assert "Learning Rate" in tab.description
        assert "0.001" in tab.description

    def test_extract_tables_minimal(self, minimal_parser):
        paper = minimal_parser.parse()
        assert paper.tables == []

    # -- Identifiers --

    def test_extract_doi(self, parser):
        paper = parser.parse()
        assert paper.doi == "10.1234/example.2025"

    def test_extract_arxiv_id_empty_when_missing(self, parser):
        paper = parser.parse()
        assert paper.arxiv_id is None

    # -- Edge cases --

    def test_empty_xml_fails(self):
        with pytest.raises(Exception):
            GrobidTEIParser("")

    def test_non_xml_input_fails(self):
        with pytest.raises(Exception):
            GrobidTEIParser("not xml at all")


# =============================================================================
# GrobidClient tests (no live server)
# =============================================================================

class TestGrobidClient:
    """Tests for GrobidClient (offline / structural only)."""

    def test_client_default_url(self):
        client = GrobidClient()
        assert client.base_url == "http://localhost:8070"
        assert client.timeout == 120

    def test_client_custom_url(self):
        client = GrobidClient(base_url="http://grobid:8080", timeout=60)
        assert client.base_url == "http://grobid:8080"
        assert client.timeout == 60

    def test_client_url_trailing_slash_stripped(self):
        client = GrobidClient(base_url="http://localhost:8070/")
        assert client.base_url == "http://localhost:8070"

    def test_is_alive_returns_false_when_unreachable(self):
        client = GrobidClient(base_url="http://127.0.0.1:19999", timeout=1)
        assert client.is_alive() is False

    def test_pdf_to_tei_file_not_found(self):
        client = GrobidClient()
        with pytest.raises(FileNotFoundError, match="not found"):
            client.pdf_to_tei("/nonexistent/path/paper.pdf")
