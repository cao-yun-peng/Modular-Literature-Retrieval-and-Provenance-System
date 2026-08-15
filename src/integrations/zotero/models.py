"""Source-neutral data contracts used by the read-only Zotero adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceAttachment:
    """A local PDF attachment associated with an external source item."""

    key: str
    local_path: Path
    version: str = ""
    content_type: str = "application/pdf"

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("SourceAttachment.key cannot be empty")


@dataclass(frozen=True)
class SourceDocument:
    """One indexable source document and the Zotero identities that own it."""

    item_key: str
    attachment: SourceAttachment
    title: str = ""
    creators: tuple[str, ...] = ()
    year: str = ""
    doi: str = ""
    collection_keys: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    item_version: str = ""
    citation_key: str | None = None
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_key.strip():
            raise ValueError("SourceDocument.item_key cannot be empty")

    @property
    def attachment_key(self) -> str:
        """Return the Zotero attachment key used by the Agent handoff."""
        return self.attachment.key

    def to_ingestion_metadata(self) -> dict[str, Any]:
        """Return namespaced metadata safe to attach to every project chunk."""
        metadata: dict[str, Any] = {
            "source_type": "zotero",
            "zotero_item_key": self.item_key,
            "zotero_attachment_key": self.attachment_key,
            "zotero_item_version": self.item_version,
            "zotero_attachment_version": self.attachment.version,
            "zotero_title": self.title,
            "zotero_year": self.year,
            "zotero_doi": self.doi,
            "title": self.title,
            "authors": list(self.creators),
            "year": self.year,
            "doi": self.doi,
            "source_version": f"{self.item_version}:{self.attachment.version}",
        }
        if self.citation_key:
            metadata["citation_key"] = self.citation_key
            metadata["citation_key_source"] = "zotero"
        if self.creators:
            metadata["zotero_creators"] = list(self.creators)
        if self.collection_keys:
            metadata["zotero_collection_keys"] = list(self.collection_keys)
        if self.tags:
            metadata["zotero_tags"] = list(self.tags)
        for key, value in self.extra_metadata.items():
            if key.startswith("zotero_") and key not in metadata:
                metadata[key] = value
        return metadata
