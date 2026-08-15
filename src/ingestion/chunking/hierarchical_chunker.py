"""Parent–Child metadata overlay for the existing document chunker."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from src.core.settings import Settings
from src.core.types import Chunk, Document
from src.ingestion.chunking.document_chunker import DocumentChunker


@dataclass(frozen=True)
class SectionParent:
    """A derived section used for context recovery, not dense/BM25 recall."""

    parent_id: str
    document_id: str
    text: str
    section_path: tuple[str, ...]
    child_ids: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    metadata: dict[str, Any]


class HierarchicalDocumentChunker:
    """Reuse current structure-aware splitting and add stable Parent relations.

    The existing paper chunker already preserves title/abstract, GROBID
    sections, figures and tables.  This adapter changes its child size through
    an immutable settings copy, then groups sequential children into parents.
    With the feature flag off, the original ``DocumentChunker`` is still used.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.ingestion is None:
            raise ValueError("Hierarchical chunking requires ingestion settings")
        config = settings.ingestion.hierarchical_chunking
        child_ingestion = replace(
            settings.ingestion,
            chunk_size=config.child_size,
            chunk_overlap=config.child_overlap,
        )
        self._base = DocumentChunker(replace(settings, ingestion=child_ingestion))
        self.parent_size = config.parent_size
        self.schema_version = config.corpus_schema_version
        self.last_parents: list[SectionParent] = []

    def split_document(self, document: Document) -> list[Chunk]:
        chunks = self._base.split_document(document)
        groups = self._group_children(chunks)
        parents: list[SectionParent] = []

        for group_index, group in enumerate(groups):
            section_path = self._section_path(group[0].metadata)
            parent_text = "\n\n".join(chunk.text for chunk in group)
            digest = hashlib.sha256(
                f"{document.id}:{group_index}:{'/'.join(section_path)}".encode()
            ).hexdigest()[:12]
            parent_id = f"section_{document.id}_{group_index:04d}_{digest}"
            pages = [page for chunk in group if (page := self._page(chunk.metadata)) is not None]
            page_start = min(pages) if pages else None
            page_end = max(pages) if pages else None

            for chunk in group:
                chunk.metadata.update(
                    {
                        "document_id": document.id,
                        "parent_id": parent_id,
                        "section_path": list(section_path),
                        "retrieval_text_version": "hierarchical-v1",
                        "corpus_schema_version": self.schema_version,
                    }
                )
                if page_start is not None:
                    chunk.metadata["page_start"] = page_start
                    chunk.metadata["page_end"] = page_end

            parent_metadata = {
                key: value
                for key, value in document.metadata.items()
                if key
                in {
                    "source_type",
                    "source_path",
                    "title",
                    "doi",
                    "citation_key",
                    "zotero_item_key",
                    "zotero_attachment_key",
                    "zotero_title",
                    "zotero_year",
                }
            }
            parents.append(
                SectionParent(
                    parent_id=parent_id,
                    document_id=document.id,
                    text=parent_text,
                    section_path=section_path,
                    child_ids=tuple(chunk.id for chunk in group),
                    page_start=page_start,
                    page_end=page_end,
                    metadata=parent_metadata,
                )
            )

        for index, chunk in enumerate(chunks):
            chunk.metadata["previous_chunk_id"] = chunks[index - 1].id if index else ""
            chunk.metadata["next_chunk_id"] = (
                chunks[index + 1].id if index + 1 < len(chunks) else ""
            )

        self.last_parents = parents
        return chunks

    def _group_children(self, chunks: list[Chunk]) -> list[list[Chunk]]:
        groups: list[list[Chunk]] = []
        current: list[Chunk] = []
        current_size = 0
        current_section: tuple[str, ...] = ()
        for chunk in chunks:
            section = self._section_path(chunk.metadata)
            section_changed = bool(current and section != current_section)
            size_exceeded = bool(
                current and current_size + len(chunk.text) > self.parent_size
            )
            if section_changed or size_exceeded:
                groups.append(current)
                current = []
                current_size = 0
            if not current:
                current_section = section
            current.append(chunk)
            current_size += len(chunk.text)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _section_path(metadata: dict[str, Any]) -> tuple[str, ...]:
        raw = metadata.get("section_path") or metadata.get("section") or ()
        if isinstance(raw, str):
            return tuple(part.strip() for part in raw.split("/") if part.strip())
        if isinstance(raw, (list, tuple)):
            return tuple(str(part).strip() for part in raw if str(part).strip())
        return ()

    @staticmethod
    def _page(metadata: dict[str, Any]) -> int | None:
        raw = metadata.get("page_start", metadata.get("page", metadata.get("page_num")))
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
