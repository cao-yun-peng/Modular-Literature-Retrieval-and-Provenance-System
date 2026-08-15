"""Versioned, source-traceable response bundle for external Agents."""

from __future__ import annotations

from typing import Any

from src.core.query_engine.fulltext_handoff_policy import HandoffDecision
from src.core.response.citation_generator import Citation
from src.core.types import RetrievalResult


class EvidenceBundleBuilder:
    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        *,
        include_score_breakdown: bool = True,
        include_zotero_identity: bool = True,
    ) -> None:
        self.include_score_breakdown = include_score_breakdown
        self.include_zotero_identity = include_zotero_identity

    def build(
        self,
        *,
        query: str,
        collection: str,
        requested_mode: str,
        selected_mode: str,
        results: list[RetrievalResult],
        citations: list[Citation],
        decision: HandoffDecision,
        fallback: bool = False,
        candidate_count: int | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "query": query,
            "retrieval": {
                "requested_mode": requested_mode,
                "selected_mode": selected_mode,
                "collection": collection,
                "candidate_count": candidate_count
                if candidate_count is not None
                else len(results),
                "result_count": len(results),
                "fallback": fallback,
            },
            "evidence": [self._evidence(result) for result in results],
            "coverage": decision.coverage_dict(),
            "recommended_next_action": decision.action_dict(),
            "citations": [citation.to_dict() for citation in citations],
        }

    def _evidence(self, result: RetrievalResult) -> dict[str, Any]:
        metadata = result.metadata or {}
        page_start = self._integer(
            metadata.get("page_start", metadata.get("page", metadata.get("page_num")))
        )
        page_end = self._integer(metadata.get("page_end"))
        if page_end is None:
            page_end = page_start
        section_path = self._list(metadata.get("section_path", metadata.get("section")))
        scores = {
            "dense": metadata.get("dense_score"),
            "bm25": metadata.get("bm25_score"),
            "rrf": metadata.get("original_score", result.score),
            "rerank": metadata.get("rerank_score"),
            "final": result.score,
        }
        evidence = {
            "evidence_id": result.chunk_id,
            "text": result.text,
            "expanded_context": metadata.get("expanded_context"),
            "document_id": metadata.get("document_id", metadata.get("source_ref")),
            "chunk_id": result.chunk_id,
            "parent_id": metadata.get("parent_id"),
            "title": metadata.get("zotero_title", metadata.get("title")),
            "section_path": section_path,
            "page_start": page_start,
            "page_end": page_end,
            "source_type": metadata.get("source_type", "manual"),
            "citation_key": metadata.get("citation_key"),
        }
        if self.include_zotero_identity:
            evidence["zotero_item_key"] = metadata.get("zotero_item_key")
            evidence["zotero_attachment_key"] = metadata.get(
                "zotero_attachment_key"
            )
        if self.include_score_breakdown:
            evidence["scores"] = scores
        return evidence

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            return int(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item)]
        if isinstance(value, str) and value:
            return [part.strip() for part in value.split(",") if part.strip()]
        return []
