"""Source-neutral contracts for future manual or external adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.integrations.zotero.models import SourceAttachment, SourceDocument


@dataclass(frozen=True)
class SourceScope:
    """Explicit source scope; integrations must not silently scan everything."""

    collection_key: str

    def __post_init__(self) -> None:
        if not self.collection_key.strip():
            raise ValueError("SourceScope.collection_key cannot be empty")


class DocumentSourceAdapter(Protocol):
    """Minimal read-only source interface consumed by synchronization."""

    def list_documents(self, collection_key: str | None = None) -> list[SourceDocument]: ...

    def get_attachment_file_path(self, attachment_key: str) -> Path: ...

    def get_attachment(self, document: SourceDocument) -> SourceAttachment: ...

    def get_version(self, document: SourceDocument) -> str: ...
