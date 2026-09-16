"""Physical-page PDF text and image locations for source-preserving token chunks."""

from __future__ import annotations

from src.core.types import Document
from src.libs.loader.pdf_loader import PdfLoader


class PageAwarePdfLoader(PdfLoader):
    def load(self, file_path):
        import fitz

        path = self._validate_file(file_path)
        doc_hash = self._compute_file_hash(path)
        parts, ranges, offset = [], [], 0
        with fitz.open(path) as pdf:
            title = (pdf.metadata or {}).get("title", "")
            for page in pdf:
                text = page.get_text() + "\n"
                parts.append(text)
                ranges.append({"page": page.number + 1, "start": offset, "end": offset + len(text)})
                offset += len(text)
        text = "".join(parts)
        images = []
        if self.extract_images:
            _, images = self._extract_and_process_images(path, "", doc_hash)
        return Document(
            id="doc_" + doc_hash[:16],
            text=text,
            metadata={
                "source_path": str(path),
                "doc_hash": doc_hash,
                "paper_mode": True,
                "doc_type": "paper",
                "title": title or self._extract_title(text),
                "page_ranges": ranges,
                "page_count": len(ranges),
                "images": images,
                "parser": "pymupdf-physical-pages-v1",
            },
        )
