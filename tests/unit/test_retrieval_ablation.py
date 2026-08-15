from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.evaluate_retrieval_ablation import parse_args
from src.core.types import RetrievalResult
from src.observability.evaluation.eval_runner import GoldenTestCase
from src.observability.evaluation.retrieval_ablation import (
    AblationConfig,
    compare_counts,
    percentile,
    rank_positions,
    run_ablation,
)


class FakeHybridSearch:
    def __init__(self, results, *, fallback=False):
        self.results = results
        self.fallback = fallback
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(
            results=self.results[: kwargs["top_k"]],
            used_fallback=self.fallback,
            dense_error="dense failed" if self.fallback else None,
            sparse_error=None,
        )


class ReverseReranker:
    def rerank(self, query, candidates, top_k):
        return [
            {**candidate, "rerank_score": float(index)}
            for index, candidate in enumerate(reversed(candidates), start=1)
        ][:top_k]


def _results():
    return [
        RetrievalResult(chunk_id=f"c{i}", score=1.0 / i, text=f"text {i}")
        for i in range(1, 6)
    ]


def test_config_requires_candidate_pool_at_least_top_k():
    with pytest.raises(ValueError, match="candidate_k"):
        AblationConfig(collection="papers", candidate_k=5, top_k=10).validate()


def test_cli_supports_cached_offline_model_loading():
    args = parse_args(["--collection", "papers", "--offline"])
    assert args.collection == "papers"
    assert args.offline is True


def test_rank_positions_includes_missing_expected_ids():
    assert rank_positions(["a", "b"], ["b", "c"]) == {"b": 2, "c": None}


def test_percentile_interpolates():
    assert percentile([10.0, 20.0, 30.0], 0.5) == 20.0
    assert percentile([10.0, 20.0], 0.95) == pytest.approx(19.5)


def test_run_ablation_uses_one_shared_candidate_set():
    hybrid = FakeHybridSearch(_results())
    config = AblationConfig(collection="papers", candidate_k=5, top_k=3)
    cases = [GoldenTestCase(query="question", expected_chunk_ids=["c5"])]

    summary, rows = run_ablation(
        cases=cases,
        hybrid_search=hybrid,
        reranker=ReverseReranker(),
        config=config,
    )

    assert hybrid.calls[0][1]["top_k"] == 5
    assert hybrid.calls[0][1]["return_details"] is True
    assert rows[0]["candidate_ids"] == ["c1", "c2", "c3", "c4", "c5"]
    assert rows[0]["rrf_top_ids"] == ["c1", "c2", "c3"]
    assert rows[0]["cross_encoder_top_ids"] == ["c5", "c4", "c3"]
    assert rows[0]["cross_encoder_candidate_ids"] == ["c5", "c4", "c3", "c2", "c1"]
    assert summary["metric_delta"]["mrr"] == 1.0
    assert summary["fallback_count"] == 0


def test_run_ablation_rejects_retrieval_fallback():
    hybrid = FakeHybridSearch(_results(), fallback=True)
    with pytest.raises(RuntimeError, match="complete RRF"):
        run_ablation(
            cases=[GoldenTestCase(query="question", expected_chunk_ids=["c1"])],
            hybrid_search=hybrid,
            reranker=ReverseReranker(),
            config=AblationConfig(collection="papers", candidate_k=5, top_k=3),
        )


def test_compare_counts_reports_improved_unchanged_and_degraded():
    rows = [
        {"rrf_metrics": {"mrr": 0.5}, "cross_encoder_metrics": {"mrr": 1.0}},
        {"rrf_metrics": {"mrr": 0.5}, "cross_encoder_metrics": {"mrr": 0.5}},
        {"rrf_metrics": {"mrr": 1.0}, "cross_encoder_metrics": {"mrr": 0.5}},
    ]
    assert compare_counts(rows, "mrr") == {
        "improved": 1,
        "unchanged": 1,
        "degraded": 1,
    }
