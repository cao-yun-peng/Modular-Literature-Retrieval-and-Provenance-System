"""Deterministic evidence de-duplication before context expansion."""

from __future__ import annotations

import hashlib
from typing import Any

from src.core.types import RetrievalResult


class EvidenceDeduplicator:
    """Remove exact repeats and cap domination by one Parent section."""

    def __init__(self, max_per_parent: int = 2) -> None:
        if max_per_parent < 1:
            raise ValueError("max_per_parent must be positive")
        self.max_per_parent = max_per_parent

    def deduplicate(
        self,
        results: list[RetrievalResult],
        *,
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalResult]:
        seen_ids: set[str] = set()
        seen_text: set[str] = set()
        parent_counts: dict[str, int] = {}
        kept: list[RetrievalResult] = []
        dropped = 0

        for result in results:
            normalized = " ".join(result.text.lower().split())
            fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            parent_id = str(result.metadata.get("parent_id", ""))
            if result.chunk_id in seen_ids or fingerprint in seen_text:
                dropped += 1
                continue
            if parent_id and parent_counts.get(parent_id, 0) >= self.max_per_parent:
                dropped += 1
                continue
            seen_ids.add(result.chunk_id)
            seen_text.add(fingerprint)
            if parent_id:
                parent_counts[parent_id] = parent_counts.get(parent_id, 0) + 1
            kept.append(result)
            if len(kept) >= top_k:
                break

        if trace is not None:
            trace.record_stage(
                "candidate_deduplication",
                {
                    "input_count": len(results),
                    "output_count": len(kept),
                    "dropped_count": dropped,
                    "max_per_parent": self.max_per_parent,
                },
            )
        return kept
