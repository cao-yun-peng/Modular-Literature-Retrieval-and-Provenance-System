"""Safe attachment of external source identities to loaded documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.core.types import Document

_RESERVED_METADATA_KEYS = {
    "source_path",
    "source",
    "images",
    "chunk_index",
    "source_ref",
    "source_doc_id",
}


def attach_source_metadata(
    document: Document, source_metadata: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Attach safe external-source metadata without breaking core contracts.

    ``DocumentChunker`` copies document metadata to every child chunk.  This
    helper is deliberately independent from the heavyweight pipeline so source
    adapters can be tested without initializing loaders or embedding clients.
    """
    if source_metadata is None:
        return {}
    if not isinstance(source_metadata, Mapping):
        raise TypeError("source_metadata must be a mapping when provided")

    safe_metadata: dict[str, Any] = {}
    for key, value in source_metadata.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("source_metadata keys must be non-empty strings")
        if key in _RESERVED_METADATA_KEYS:
            raise ValueError(f"source_metadata cannot override reserved key: {key}")
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_metadata[key] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, int, float, bool)) or item is None
            for item in value
        ):
            safe_metadata[key] = list(value)
        else:
            raise TypeError(
                "source_metadata values must be scalar values or flat sequences"
            )

    document.metadata.update(safe_metadata)
    return safe_metadata
