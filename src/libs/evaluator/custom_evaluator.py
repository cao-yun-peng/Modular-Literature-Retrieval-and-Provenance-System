"""Deterministic information-retrieval metrics for regression evaluation."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.libs.evaluator.base_evaluator import BaseEvaluator


class CustomEvaluator(BaseEvaluator):
    """Custom evaluator for deterministic retrieval metrics.

    The evaluator expects retrieved chunks to contain an identifier field.
    Supported id fields: id, chunk_id, document_id, doc_id.

    ``precision_at_k``, ``recall_at_k`` and ``ndcg_at_k`` use the requested
    ``top_k`` when the caller supplies it, otherwise the number of retrieved
    chunks. Empty retrieval is a valid scored result; missing ground truth is
    not, because treating an unlabelled case as a zero would silently corrupt
    the aggregate quality score.
    """

    SUPPORTED_METRICS = {
        "hit_rate",
        "mrr",
        "precision_at_k",
        "recall_at_k",
        "ndcg_at_k",
    }
    _ID_FIELDS = ("id", "chunk_id", "document_id", "doc_id")

    def __init__(
        self,
        settings: Any = None,
        metrics: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> None:
        self.settings = settings
        self.kwargs = kwargs

        if metrics is None:
            metrics = self._metrics_from_settings(settings)

        normalized = [str(metric).strip().lower() for metric in (metrics or [])]
        if not normalized:
            normalized = [
                "hit_rate",
                "mrr",
                "precision_at_k",
                "recall_at_k",
                "ndcg_at_k",
            ]

        unsupported = [metric for metric in normalized if metric not in self.SUPPORTED_METRICS]
        if unsupported:
            raise ValueError(
                "Unsupported custom metrics: "
                f"{', '.join(unsupported)}. Supported: {', '.join(sorted(self.SUPPORTED_METRICS))}"
            )

        self.metrics = normalized

    def evaluate(
        self,
        query: str,
        retrieved_chunks: List[Any],
        generated_answer: Optional[str] = None,
        ground_truth: Optional[Any] = None,
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, float]:
        """Compute requested metrics for the given retrieval results.

        Args:
            query: The user query string.
            retrieved_chunks: Retrieved chunks or records.
            generated_answer: Optional generated answer (unused).
            ground_truth: Ground truth ids or structure.
            trace: Optional TraceContext (unused).
            **kwargs: Additional parameters (unused).

        Returns:
            Dictionary of metric name to float value.
        """
        self.validate_query(query)
        if not isinstance(retrieved_chunks, list):
            raise ValueError("retrieved_chunks must be a list")

        retrieved_ids = self._extract_ids(retrieved_chunks, label="retrieved_chunks")
        ground_truth_ids = self._extract_ground_truth_ids(ground_truth)
        if not ground_truth_ids:
            raise ValueError(
                "Deterministic retrieval metrics require at least one ground-truth id. "
                "Add expected_chunk_ids/supporting_chunk_ids to this test case."
            )

        results: Dict[str, float] = {}
        requested_top_k = kwargs.get("top_k")
        effective_k = int(requested_top_k) if requested_top_k else len(retrieved_ids)
        if effective_k < 0:
            raise ValueError("top_k cannot be negative")

        if "hit_rate" in self.metrics:
            results["hit_rate"] = self._compute_hit_rate(retrieved_ids, ground_truth_ids)
        if "mrr" in self.metrics:
            results["mrr"] = self._compute_mrr(retrieved_ids, ground_truth_ids)
        if "precision_at_k" in self.metrics:
            results["precision_at_k"] = self._compute_precision_at_k(
                retrieved_ids, ground_truth_ids, effective_k
            )
        if "recall_at_k" in self.metrics:
            results["recall_at_k"] = self._compute_recall_at_k(
                retrieved_ids, ground_truth_ids, effective_k
            )
        if "ndcg_at_k" in self.metrics:
            results["ndcg_at_k"] = self._compute_ndcg_at_k(
                retrieved_ids, ground_truth_ids, effective_k
            )

        return results

    def _metrics_from_settings(self, settings: Any) -> List[str]:
        """Extract metrics list from settings if available."""
        if settings is None:
            return []
        metrics = getattr(getattr(settings, "evaluation", None), "metrics", None)
        if metrics is None:
            return []
        return [str(metric) for metric in metrics]

    def _extract_ground_truth_ids(self, ground_truth: Optional[Any]) -> List[str]:
        """Extract ground truth ids from various input shapes."""
        if ground_truth is None:
            return []
        if isinstance(ground_truth, str):
            return [ground_truth]
        if isinstance(ground_truth, dict):
            if "ids" in ground_truth and isinstance(ground_truth["ids"], list):
                return self._extract_ids(ground_truth["ids"], label="ground_truth.ids")
            return self._extract_ids([ground_truth], label="ground_truth")
        if isinstance(ground_truth, list):
            return self._extract_ids(ground_truth, label="ground_truth")

        raise ValueError(
            f"Unsupported ground_truth type: {type(ground_truth).__name__}. "
            "Expected str, dict, list, or None."
        )

    def _extract_ids(self, items: Iterable[Any], label: str) -> List[str]:
        """Extract ids from a list of items."""
        ids: List[str] = []
        for index, item in enumerate(items):
            if isinstance(item, str):
                ids.append(item)
                continue
            if isinstance(item, dict):
                for field in self._ID_FIELDS:
                    if field in item:  #item字典dict数据类型
                        ids.append(str(item[field]))
                        break
                else:
                    raise ValueError(
                        f"Missing id field in {label}[{index}]. "
                        f"Expected one of {', '.join(self._ID_FIELDS)}"
                    )
                continue
            if hasattr(item, "chunk_id"):
                ids.append(str(getattr(item, "chunk_id")))
                continue
            if hasattr(item, "id"):
                ids.append(str(getattr(item, "id")))
                continue

            raise ValueError(
                f"Unable to extract id from {label}[{index}] of type "
                f"{type(item).__name__}"
            )

        return ids

    def _compute_hit_rate(self, retrieved_ids: Sequence[str], ground_truth_ids: Sequence[str]) -> float:
        """Compute hit rate (binary)."""
        if not ground_truth_ids:
            return 0.0
        return 1.0 if any(item in ground_truth_ids for item in retrieved_ids) else 0.0

    def _compute_mrr(self, retrieved_ids: Sequence[str], ground_truth_ids: Sequence[str]) -> float:
        """Compute Mean Reciprocal Rank (MRR)."""
        if not ground_truth_ids:
            return 0.0
        for rank, item in enumerate(retrieved_ids, start=1):
            if item in ground_truth_ids:
                return 1.0 / rank
        return 0.0

    @staticmethod
    def _unique(items: Sequence[str]) -> List[str]:
        """Return items in first-seen order without duplicate identifiers."""
        return list(dict.fromkeys(items))

    def _compute_precision_at_k(
        self,
        retrieved_ids: Sequence[str],
        ground_truth_ids: Sequence[str],
        k: int,
    ) -> float:
        """Compute binary Precision@K over unique retrieved identifiers."""
        retrieved = self._unique(retrieved_ids)
        if not retrieved or k <= 0:
            return 0.0
        relevant = set(ground_truth_ids)
        hits = sum(1 for item in retrieved[:k] if item in relevant)
        return hits / k

    def _compute_recall_at_k(
        self,
        retrieved_ids: Sequence[str],
        ground_truth_ids: Sequence[str],
        k: int,
    ) -> float:
        """Compute binary Recall@K over unique ground-truth identifiers."""
        relevant = set(ground_truth_ids)
        if not relevant:
            return 0.0
        hits = len(set(retrieved_ids[:k]) & relevant)
        return hits / len(relevant)

    def _compute_ndcg_at_k(
        self,
        retrieved_ids: Sequence[str],
        ground_truth_ids: Sequence[str],
        k: int,
    ) -> float:
        """Compute binary nDCG@K, de-duplicating repeated retrieved IDs."""
        retrieved = self._unique(retrieved_ids)
        if not retrieved or k <= 0:
            return 0.0

        relevant = set(ground_truth_ids)
        dcg = sum(
            1.0 / math.log2(rank + 1)
            for rank, item in enumerate(retrieved[:k], start=1)
            if item in relevant
        )
        ideal_hits = min(len(relevant), k)
        idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
        return dcg / idcg if idcg else 0.0
