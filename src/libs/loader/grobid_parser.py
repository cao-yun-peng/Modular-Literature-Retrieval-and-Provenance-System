"""GROBID TEI XML parser for structured academic paper metadata extraction.

Parses TEI XML output from GROBID (https://github.com/kermitt2/grobid) into
structured Python objects: Paper, Section, FigureOrTable.

Usage:
    >>> client = GrobidClient("http://localhost:8070")
    >>> tei_xml = client.pdf_to_tei("paper.pdf")
    >>> parser = GrobidTEIParser(tei_xml)
    >>> paper = parser.parse()
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from lxml import etree

logger = logging.getLogger(__name__)

TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _ns(tag: str) -> str:
    return f"{{{TEI_NS}}}{tag}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Section:
    heading: str
    paragraphs: List[str] = field(default_factory=list)
    level: int = 1


@dataclass
class FigureOrTable:
    id: str
    type: str  # "figure" or "table"
    caption: str
    description: str


@dataclass
class Paper:
    title: str = ""
    authors: List[str] = field(default_factory=list)
    abstract: str = ""
    sections: List[Section] = field(default_factory=list)
    figures: List[FigureOrTable] = field(default_factory=list)
    tables: List[FigureOrTable] = field(default_factory=list)
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# GROBID TEI Parser
# ---------------------------------------------------------------------------

class GrobidTEIParser:
    """Parse GROBID TEI XML into structured Paper object."""

    def __init__(self, tei_xml: str):
        self.root = etree.fromstring(tei_xml.encode("utf-8"))
        self._fig_counter = 0
        self._tab_counter = 0

    def parse(self) -> Paper:
        return Paper(
            title=self._extract_title(),
            authors=self._extract_authors(),
            abstract=self._extract_abstract(),
            sections=self._extract_sections(),
            figures=self._extract_figures(),
            tables=self._extract_tables(),
            doi=self._extract_doi(),
            arxiv_id=self._extract_arxiv_id(),
        )

    # ------------------------------------------------------------------
    # Title / Authors / Abstract
    # ------------------------------------------------------------------

    def _extract_title(self) -> str:
        titles = self.root.xpath(
            "//tei:teiHeader//tei:titleStmt/tei:title/text()",
            namespaces={"tei": TEI_NS},
        )
        return titles[0].strip() if titles else ""

    def _extract_authors(self) -> List[str]:
        authors = []
        for author in self.root.xpath(
            "//tei:sourceDesc//tei:author", namespaces={"tei": TEI_NS}
        ):
            forename = author.xpath(
                "tei:persName/tei:forename/text()", namespaces={"tei": TEI_NS}
            )
            surname = author.xpath(
                "tei:persName/tei:surname/text()", namespaces={"tei": TEI_NS}
            )
            if forename and surname:
                authors.append(f"{forename[0]} {surname[0]}")
            else:
                text = "".join(author.itertext()).strip()
                if text:
                    authors.append(text)
        return authors

    def _extract_abstract(self) -> str:
        parts = self.root.xpath(
            "//tei:profileDesc//tei:abstract//text()",
            namespaces={"tei": TEI_NS},
        )
        return " ".join(p.strip() for p in parts if p.strip())

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _extract_sections(self) -> List[Section]:
        sections: List[Section] = []
        body = self.root.find(f'.//{_ns("text")}/{_ns("body")}')
        if body is None:
            return sections
        for div in body.findall(_ns("div")):
            self._collect_sections(div, sections, level=1)
        return sections

    def _collect_sections(self, div, sections: List[Section], level: int) -> None:
        head = div.find(_ns("head"))
        heading = ""
        if head is not None:
            heading = "".join(head.itertext()).strip()
            heading = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading).strip()

        paragraphs = []
        for p in div.xpath("tei:p", namespaces={"tei": TEI_NS}):
            text = "".join(p.itertext()).strip()
            if text:
                paragraphs.append(text)

        sections.append(Section(heading=heading, paragraphs=paragraphs, level=level))

        for sub_div in div.findall(_ns("div")):
            self._collect_sections(sub_div, sections, level=level + 1)

    # ------------------------------------------------------------------
    # Figures / Tables
    # ------------------------------------------------------------------

    def _extract_figures(self) -> List[FigureOrTable]:
        figs = []
        for fig in self.root.xpath("//tei:figure", namespaces={"tei": TEI_NS}):
            self._fig_counter += 1
            fid = self._get_asset_id(fig, "fig") or f"fig_{self._fig_counter}"
            head = fig.find(_ns("head"))
            caption = "".join(head.itertext()).strip() if head is not None else ""
            desc_parts = []
            for desc in fig.findall(_ns("figDesc")):
                desc_parts.append("".join(desc.itertext()).strip())
            figs.append(
                FigureOrTable(
                    id=fid,
                    type="figure",
                    caption=caption,
                    description="\n".join(desc_parts),
                )
            )
        return figs

    def _extract_tables(self) -> List[FigureOrTable]:
        tables = []
        for tab in self.root.xpath("//tei:table", namespaces={"tei": TEI_NS}):
            self._tab_counter += 1
            tid = self._get_asset_id(tab, "tab") or f"tab_{self._tab_counter}"
            head = tab.find(_ns("head"))
            caption = "".join(head.itertext()).strip() if head is not None else ""
            rows = tab.xpath(".//tei:row", namespaces={"tei": TEI_NS})
            content_lines = []
            for row in rows:
                cells = row.xpath("tei:cell/text()", namespaces={"tei": TEI_NS})
                content_lines.append("\t".join(cells))
            tables.append(
                FigureOrTable(
                    id=tid,
                    type="table",
                    caption=caption,
                    description="\n".join(content_lines),
                )
            )
        return tables

    # ------------------------------------------------------------------
    # Identifiers
    # ------------------------------------------------------------------

    def _extract_doi(self) -> Optional[str]:
        for idno in self.root.xpath(
            "//tei:sourceDesc//tei:idno[@type='DOI']/text()",
            namespaces={"tei": TEI_NS},
        ):
            return idno.strip()
        # Fallback: search full text for DOI pattern
        full_text = " ".join(self.root.itertext())
        match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", full_text, re.IGNORECASE)
        return match.group(0).rstrip(".,;:") if match else None

    def _extract_arxiv_id(self) -> Optional[str]:
        for idno in self.root.xpath(
            "//tei:sourceDesc//tei:idno[@type='arXiv']/text()",
            namespaces={"tei": TEI_NS},
        ):
            return idno.strip()
        full_text = " ".join(self.root.itertext())
        match = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", full_text, re.IGNORECASE)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_asset_id(self, elem, prefix: str) -> str:
        xml_id = elem.attrib.get(f"{{{XML_NS}}}id")
        if xml_id:
            return xml_id
        return ""


# ---------------------------------------------------------------------------
# GROBID API Client
# ---------------------------------------------------------------------------

class GrobidClient:
    """HTTP client for GROBID academic PDF parsing service.

    Args:
        base_url: GROBID server base URL (default http://localhost:8070).
        timeout: Request timeout in seconds.
    """

    def __init__(self, base_url: str = "http://localhost:8070", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_alive(self) -> bool:
        try:
            import requests
            r = requests.get(
                f"{self.base_url}/api/isalive",
                timeout=5,
                proxies={"http": None, "https": None},
            )
            r.raise_for_status()
            return True
        except Exception:
            return False

    def pdf_to_tei(
        self,
        pdf_path: str,
        consolidate_header: bool = True,
        process_formula: bool = True,
    ) -> str:
        import os
        import requests

        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        params = {
            "consolidateHeader": "1" if consolidate_header else "0",
            "processFormula": "true" if process_formula else "false",
        }
        with open(pdf_path, "rb") as f:
            files = {"input": f}
            resp = requests.post(
                f"{self.base_url}/api/processFulltextDocument",
                files=files,
                params=params,
                timeout=self.timeout,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
        return resp.text
