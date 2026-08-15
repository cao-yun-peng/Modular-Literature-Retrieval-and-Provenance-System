"""Recover Parent or neighboring context for retrieved Child chunks."""

from __future__ import annotations

from typing import Any

from src.core.types import RetrievalResult
from src.ingestion.storage.section_store import SectionStore


class ContextExpander:
    """Attach expansion context while preserving the original evidence text."""

    MODES = {"none", "neighbors", "parent", "adaptive"}

    def __init__(
        self,
        section_store: SectionStore | None,
        vector_store: Any | None,
        max_context_characters: int = 6000,
    ) -> None:
        self.section_store = section_store
        self.vector_store = vector_store
        self.max_context_characters = max_context_characters

    def expand(
        self,
        results: list[RetrievalResult],
        *,
        mode: str,
        collection: str,
        trace: Any | None = None,
    ) -> list[RetrievalResult]:
        if mode not in self.MODES:
            raise ValueError(f"Unsupported expand_context mode: {mode}")
        if mode == "none" or self.section_store is None:
            return results

        remaining = self.max_context_characters
        expanded: list[RetrievalResult] = []
        expansion_count = 0
        for result in results:
            metadata = dict(result.metadata)
            context: dict[str, Any] | None = None
            parent = self.section_store.get_parent_for_child(collection, result.chunk_id)
            selected = mode
            if mode == "adaptive":
                selected = (
                    "parent"
                    if parent is not None and len(parent.text) <= max(remaining, 0)
                    else "neighbors"
                )
            if selected == "parent" and parent is not None and remaining > 0:
                text = parent.text[:remaining]
                context = {
                    "type": "parent",
                    "parent_id": parent.parent_id,
                    "text": text,
                    "child_ids": list(parent.child_ids),
                    "truncated": len(text) < len(parent.text),
                }
            elif selected == "neighbors" and remaining > 0:
                context = self._neighbor_context(
                    result.chunk_id, collection, remaining
                )
            if context:
                remaining -= len(str(context.get("text", "")))
                metadata["expanded_context"] = context
                expansion_count += 1
            expanded.append(
                RetrievalResult(
                    chunk_id=result.chunk_id,
                    score=result.score,
                    text=result.text,
                    metadata=metadata,
                )
            )

        if trace is not None:
            trace.record_stage(
                "context_expansion",
                {
                    "requested_mode": mode,
                    "expanded_count": expansion_count,
                    "context_character_budget": self.max_context_characters,
                    "remaining_characters": remaining,
                },
            )
        return expanded

    def _neighbor_context(
        self, child_id: str, collection: str, budget: int
    ) -> dict[str, Any] | None:
        if self.vector_store is None or not hasattr(self.vector_store, "get_by_ids"):
            return None
        neighbor_ids = self.section_store.get_neighbor_ids(collection, child_id)
        if not neighbor_ids:
            return None
        records = self.vector_store.get_by_ids(list(neighbor_ids))
        texts: list[str] = []
        returned_ids: list[str] = []
        for record in records:
            if not record:
                continue
            text = str(record.get("text", ""))
            if text:
                texts.append(text)
                returned_ids.append(str(record.get("id", "")))
        combined = "\n\n".join(texts)
        if not combined:
            return None
        return {
            "type": "neighbors",
            "chunk_ids": returned_ids,
            "text": combined[:budget],
            "truncated": len(combined) > budget,
        }
