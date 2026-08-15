"""Fair, reproducible RRF versus reranker ablation utilities."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.libs.evaluator.custom_evaluator import CustomEvaluator
from src.observability.evaluation.eval_runner import GoldenTestCase

METRICS = ("hit_rate", "mrr", "precision_at_k", "recall_at_k", "ndcg_at_k")


@dataclass(frozen=True)
class AblationConfig:
    """Parameters that must remain fixed across both ranking arms."""

    collection: str
    candidate_k: int = 20
    top_k: int = 10
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def validate(self) -> None:
        if self.top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k")
        if not self.collection.strip():
            raise ValueError("collection cannot be empty")
        if not self.model.strip():
            raise ValueError("model cannot be empty")


def retrieval_to_candidate(result: Any) -> dict[str, Any]:
    """Convert the shared retrieval contract to the reranker contract."""
    return {
        "id": str(result.chunk_id),
        "text": result.text,
        "score": float(result.score),
        "metadata": dict(result.metadata),
    }


def rank_positions(ids: Sequence[str], expected_ids: Iterable[str]) -> dict[str, int | None]:
    """Return one-based positions for every labelled evidence chunk."""
    first_positions = {chunk_id: index for index, chunk_id in enumerate(ids, start=1)}
    return {str(chunk_id): first_positions.get(str(chunk_id)) for chunk_id in expected_ids}


def mean_metrics(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    """Average a complete set of per-query metric dictionaries."""
    if not rows:
        raise ValueError("Cannot aggregate an empty metric sequence")
    return {
        metric: sum(float(row[metric]) for row in rows) / len(rows)
        for metric in METRICS
    }


def percentile(values: Sequence[float], q: float) -> float:
    """Compute a linearly interpolated percentile without third-party dependencies."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def compare_counts(
    rows: Sequence[dict[str, Any]], metric: str, tolerance: float = 1e-12
) -> dict[str, int]:
    """Count per-query improvements, ties, and regressions for one metric."""
    counts = {"improved": 0, "unchanged": 0, "degraded": 0}
    for row in rows:
        delta = float(row["cross_encoder_metrics"][metric]) - float(
            row["rrf_metrics"][metric]
        )
        if delta > tolerance:
            counts["improved"] += 1
        elif delta < -tolerance:
            counts["degraded"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def run_ablation(
    *,
    cases: Sequence[GoldenTestCase],
    hybrid_search: Any,
    reranker: Any,
    config: AblationConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate RRF and Cross-Encoder over exactly the same candidate lists.

    Retrieval fallback is rejected: a single-path result is not an RRF result and
    must not silently enter an RRF-vs-reranker experiment. Reranker exceptions are
    deliberately allowed to propagate so a fallback ordering cannot be reported
    as a Cross-Encoder score.
    """
    config.validate()
    if not cases:
        raise ValueError("Golden test set is empty")

    evaluator = CustomEvaluator(metrics=METRICS)
    per_query: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        if not case.expected_chunk_ids:
            raise ValueError(f"Case {case.case_id or index} has no expected_chunk_ids")

        retrieval_started = time.perf_counter()
        details = hybrid_search.search(
            case.query,
            top_k=config.candidate_k,
            filters={"collection": case.collection or config.collection},
            return_details=True,
        )
        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000.0
        if details.used_fallback or details.dense_error or details.sparse_error:
            raise RuntimeError(
                f"Case {case.case_id or index} did not produce a complete RRF result: "
                f"dense_error={details.dense_error!r}, sparse_error={details.sparse_error!r}"
            )

        candidates = list(details.results)
        if not candidates:
            raise RuntimeError(f"Case {case.case_id or index} returned no candidates")
        if len(candidates) < config.top_k:
            raise RuntimeError(
                f"Case {case.case_id or index} returned {len(candidates)} candidates; "
                f"at least top_k={config.top_k} are required"
            )

        rrf_top = candidates[: config.top_k]
        candidate_dicts = [retrieval_to_candidate(result) for result in candidates]
        rerank_started = time.perf_counter()
        reranked = reranker.rerank(
            case.query,
            candidate_dicts,
            top_k=len(candidate_dicts),
        )
        rerank_ms = (time.perf_counter() - rerank_started) * 1000.0
        if len(reranked) != len(candidate_dicts):
            raise RuntimeError(
                f"Cross-Encoder returned {len(reranked)} results; "
                f"expected {len(candidate_dicts)}"
            )
        if any("rerank_score" not in candidate for candidate in reranked):
            raise RuntimeError("Cross-Encoder output is missing rerank_score")

        rrf_ids = [str(result.chunk_id) for result in rrf_top]
        reranked_candidate_ids = [str(candidate["id"]) for candidate in reranked]
        reranked_top = reranked[: config.top_k]
        reranked_ids = [str(candidate["id"]) for candidate in reranked_top]
        candidate_ids = [str(result.chunk_id) for result in candidates]
        if sorted(reranked_candidate_ids) != sorted(candidate_ids):
            raise RuntimeError("Cross-Encoder output does not match the RRF candidate set")

        rrf_metrics = evaluator.evaluate(
            case.query,
            rrf_ids,
            ground_truth=case.expected_chunk_ids,
            top_k=config.top_k,
        )
        cross_encoder_metrics = evaluator.evaluate(
            case.query,
            reranked_ids,
            ground_truth=case.expected_chunk_ids,
            top_k=config.top_k,
        )
        per_query.append(
            {
                "case_index": index,
                "case_id": case.case_id,
                "query": case.query,
                "query_type": case.query_type,
                "collection": case.collection or config.collection,
                "expected_chunk_ids": list(case.expected_chunk_ids),
                "candidate_ids": candidate_ids,
                "rrf_top_ids": rrf_ids,
                "cross_encoder_top_ids": reranked_ids,
                "cross_encoder_candidate_ids": reranked_candidate_ids,
                "cross_encoder_scores": [
                    float(candidate["rerank_score"]) for candidate in reranked
                ],
                "rrf_expected_ranks": rank_positions(rrf_ids, case.expected_chunk_ids),
                "cross_encoder_expected_ranks": rank_positions(
                    reranked_ids, case.expected_chunk_ids
                ),
                "rrf_candidate_expected_ranks": rank_positions(
                    candidate_ids, case.expected_chunk_ids
                ),
                "cross_encoder_candidate_expected_ranks": rank_positions(
                    reranked_candidate_ids, case.expected_chunk_ids
                ),
                "rrf_metrics": rrf_metrics,
                "cross_encoder_metrics": cross_encoder_metrics,
                "metric_delta": {
                    metric: cross_encoder_metrics[metric] - rrf_metrics[metric]
                    for metric in METRICS
                },
                "retrieval_ms": retrieval_ms,
                "rerank_ms": rerank_ms,
            }
        )

    rrf_aggregate = mean_metrics([row["rrf_metrics"] for row in per_query])
    cross_encoder_aggregate = mean_metrics(
        [row["cross_encoder_metrics"] for row in per_query]
    )
    retrieval_times = [float(row["retrieval_ms"]) for row in per_query]
    rerank_times = [float(row["rerank_ms"]) for row in per_query]
    summary = {
        "query_count": len(per_query),
        "rrf_metrics": rrf_aggregate,
        "cross_encoder_metrics": cross_encoder_aggregate,
        "metric_delta": {
            metric: cross_encoder_aggregate[metric] - rrf_aggregate[metric]
            for metric in METRICS
        },
        "per_query_comparison": {
            metric: compare_counts(per_query, metric) for metric in ("mrr", "ndcg_at_k")
        },
        "latency_ms": {
            "retrieval_total": sum(retrieval_times),
            "retrieval_median": statistics.median(retrieval_times),
            "retrieval_p95": percentile(retrieval_times, 0.95),
            "rerank_total": sum(rerank_times),
            "rerank_median": statistics.median(rerank_times),
            "rerank_p95": percentile(rerank_times, 0.95),
            "rerank_mean": statistics.mean(rerank_times),
        },
        "fallback_count": 0,
    }
    return summary, per_query


def save_ablation_artifacts(
    *,
    output_root: str | Path,
    config: AblationConfig,
    test_set_path: str | Path,
    summary: dict[str, Any],
    per_query: Sequence[dict[str, Any]],
    package_versions: dict[str, str],
) -> Path:
    """Save a non-overwriting, self-describing ablation run."""
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    test_set = Path(test_set_path)
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "experiment": "rrf_vs_cross_encoder_same_candidates",
        "test_set_path": str(test_set_path),
        "test_set_sha256": hashlib.sha256(test_set.read_bytes()).hexdigest(),
        **asdict(config),
        "package_versions": package_versions,
        "fallback_policy": "fail_closed",
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "per_query.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in per_query),
        encoding="utf-8",
    )
    return run_dir
