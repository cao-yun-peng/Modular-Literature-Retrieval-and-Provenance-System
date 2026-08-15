"""Explainable recommendation policy for Agent-side Zotero full-text reads."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.settings import AgentHandoffSettings
from src.core.types import RetrievalResult


@dataclass(frozen=True)
class HandoffDecision:
    signal: str
    reason: str
    recommended_documents: tuple[dict[str, str], ...] = ()

    def coverage_dict(self) -> dict[str, str]:
        return {"signal": self.signal, "reason": self.reason}

    def action_dict(self) -> dict[str, object] | None:
        if not self.recommended_documents:
            return None
        first = self.recommended_documents[0]
        return {
            "tool": "zotero.fulltext",
            "zotero_item_key": first.get("zotero_item_key"),
            "zotero_attachment_key": first.get("zotero_attachment_key"),
            "recommended_documents": list(self.recommended_documents),
            "required": False,
            "project_did_not_fetch_fulltext": True,
        }


class FulltextHandoffPolicy:
    """Use deterministic intent/coverage signals; never fetch full text."""

    _GLOBAL_INTENT = re.compile(
        r"(全文|整体|主要贡献|完整论证|通篇|总结.*论文|比较.*论文|"
        r"full\s*text|overall|main contributions?|entire (paper|article)|"
        r"summari[sz]e (the )?(paper|article))",
        re.IGNORECASE,
    )

    def __init__(self, settings: AgentHandoffSettings) -> None:
        self.settings = settings

    def decide(self, query: str, results: list[RetrievalResult]) -> HandoffDecision:
        documents = self._documents(results)
        if not self.settings.enabled:
            return HandoffDecision(
                signal="not_evaluated",
                reason="agent_handoff feature is disabled",
            )
        if not results:
            return HandoffDecision(
                signal="insufficient_evidence",
                reason="no evidence was retrieved; no verified Zotero attachment can be recommended",
            )
        if (
            self.settings.global_reading_handoff
            and self._GLOBAL_INTENT.search(query)
            and documents
        ):
            return HandoffDecision(
                signal="needs_fulltext",
                reason="query requests global reading beyond local evidence chunks",
                recommended_documents=documents,
            )
        if (
            self.settings.low_coverage_handoff
            and max(result.score for result in results) < self.settings.low_score_threshold
            and documents
        ):
            return HandoffDecision(
                signal="needs_fulltext",
                reason="retrieval scores are below the configured coverage threshold",
                recommended_documents=documents,
            )
        return HandoffDecision(
            signal="evidence_available",
            reason="retrieved evidence is available; full-text reading is optional",
        )

    def _documents(
        self, results: list[RetrievalResult]
    ) -> tuple[dict[str, str], ...]:
        documents: list[dict[str, str]] = []
        seen: set[str] = set()
        for result in results:
            metadata = result.metadata
            attachment_key = str(metadata.get("zotero_attachment_key", ""))
            item_key = str(metadata.get("zotero_item_key", ""))
            if not attachment_key or attachment_key in seen:
                continue
            seen.add(attachment_key)
            documents.append(
                {
                    "zotero_item_key": item_key,
                    "zotero_attachment_key": attachment_key,
                    "title": str(metadata.get("zotero_title", metadata.get("title", ""))),
                }
            )
            if len(documents) >= self.settings.max_recommended_documents:
                break
        return tuple(documents)
