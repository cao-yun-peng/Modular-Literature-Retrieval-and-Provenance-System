"""Bounded arXiv discovery and PDF acquisition; no arbitrary URL fetches."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: list[str]
    published: str
    abstract: str
    doi: str = ""

    def __post_init__(self):
        if not _ID.fullmatch(self.arxiv_id):
            raise ValueError("Invalid arXiv ID")

    @property
    def url(self):
        return "https://arxiv.org/abs/" + self.arxiv_id

    @property
    def pdf_url(self):
        return "https://arxiv.org/pdf/" + self.arxiv_id

    def metadata(self):
        return {
            "source_type": "arxiv",
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": self.authors,
            "year": self.published[:4],
            "doi": self.doi,
            "publication_status": "preprint",
            "source_url": self.url,
        }

    def ris(self):
        def clean(value):
            return " ".join(str(value).split())

        lines = ["TY  - UNPB", f"TI  - {clean(self.title)}"]
        lines += [f"AU  - {clean(author)}" for author in self.authors]
        lines += [
            f"PY  - {clean(self.published[:4])}",
            f"UR  - {self.url}",
            f"L1  - {self.pdf_url}",
            f"N1  - arXiv:{self.arxiv_id}",
        ]
        if self.doi:
            lines.append(f"DO  - {clean(self.doi)}")
        return "\n".join(lines + ["ER  -", ""]) + "\n"


class _ArxivRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"arxiv.org", "export.arxiv.org"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Only HTTPS arxiv.org and export.arxiv.org URLs are allowed")


class ArxivClient:
    def __init__(self, *, timeout=30, max_pdf_bytes=25 * 1024 * 1024, opener=None):
        self.timeout = timeout
        self.max_pdf_bytes = max_pdf_bytes
        self.opener = opener or urllib.request.build_opener(_ArxivRedirect())

    def _read(self, url, limit):
        global _LAST_REQUEST
        validate_url(url)
        # Respect arXiv's three-second request spacing within this process.
        with _RATE_LOCK:
            time.sleep(max(0, 3 - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": "ModularRAG-Research/0.1"})
        with self.opener.open(request, timeout=self.timeout) as response:
            validate_url(response.geturl())
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Remote document exceeds size limit")
            return data

    def search(self, query, limit=3):
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("limit must be 1–5")
        # Plain terms, not LLM-authored API syntax or URLs.
        terms = re.findall(r"[\w-]+", query)[:30]
        if not terms:
            return []
        search = " AND ".join(f'all:"{term}"' for term in terms)
        params = urllib.parse.urlencode(
            {
                "search_query": search,
                "max_results": limit,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
        data = self._read("https://export.arxiv.org/api/query?" + params, 2 * 1024 * 1024)
        return self.parse_feed(data)[:limit]

    @staticmethod
    def parse_feed(data):
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("XML entities are not allowed")
        root = ET.fromstring(data)
        ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
        papers, seen = [], set()
        for entry in root.findall("a:entry", ns):
            identity = entry.findtext("a:id", "", ns).split("/abs/")[-1]
            if not _ID.fullmatch(identity):
                raise ValueError("arXiv returned an invalid entry or API error")
            canonical = re.sub(r"v\d+$", "", identity)
            if canonical in seen:
                continue
            seen.add(canonical)
            papers.append(
                Paper(
                    identity,
                    " ".join(entry.findtext("a:title", "", ns).split()),
                    [a.text or "" for a in entry.findall("a:author/a:name", ns)],
                    entry.findtext("a:published", "", ns),
                    entry.findtext("a:summary", "", ns).strip(),
                    entry.findtext("ar:doi", "", ns),
                )
            )
        return papers

    def download(self, paper, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (paper.arxiv_id.replace("/", "_") + ".pdf")
        if path.is_symlink():
            raise ValueError("Refusing symlink download destination")
        if path.exists():
            with path.open("rb") as stream:
                if path.stat().st_size <= self.max_pdf_bytes and stream.read(5) == b"%PDF-":
                    return path
            raise ValueError("Existing cached PDF is invalid")
        data = self._read(paper.pdf_url, self.max_pdf_bytes)
        if not data.startswith(b"%PDF-"):
            raise ValueError("Downloaded content is not a PDF")
        # Exclusive creation: never overwrite another run's file.
        with path.open("xb") as stream:
            stream.write(data)
        return path


class LiteratureAcquirer:
    """Download, persist provenance, index, and optionally import to Zotero.

    Each record exposes separate RAG and Zotero states. A Connector failure cannot
    erase successful ingestion. The manifest is per-run, not a global sync ledger.
    """

    def __init__(self, client, directory, ingest, *, zotero=None):
        self.client, self.directory, self.ingest, self.zotero = (
            client,
            Path(directory),
            ingest,
            zotero,
        )

    def acquire(self, query, collection, limit):
        records = []
        self.directory.mkdir(parents=True, exist_ok=True)
        for paper in self.client.search(query, limit):
            record = {
                **asdict(paper),
                "url": paper.url,
                "pdf_url": paper.pdf_url,
                "rag_status": "not_indexed",
                "zotero_status": "not_requested",
            }
            try:
                path = self.client.download(paper, self.directory)
                record.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                ingested = self.ingest(path, paper.metadata(), collection)
                if ingested.success:
                    record["rag_status"] = "indexed"
                else:
                    record["rag_status"] = "failed"
            except Exception as exc:
                record["rag_status"] = "failed"
                record["error_type"] = type(exc).__name__
            if self.zotero:
                try:
                    record["zotero_result"] = self.zotero.import_paper(paper)
                    record["zotero_status"] = "import_accepted"
                except Exception as exc:
                    record["zotero_status"] = "failed_or_uncertain"
                    record["zotero_error_type"] = type(exc).__name__
            records.append(record)
        (self.directory / "manifest.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.directory / "references.ris").write_text(
            "".join(
                Paper(
                    r["arxiv_id"], r["title"], r["authors"], r["published"], r["abstract"], r["doi"]
                ).ris()
                for r in records
            ),
            encoding="utf-8",
        )
        return records
