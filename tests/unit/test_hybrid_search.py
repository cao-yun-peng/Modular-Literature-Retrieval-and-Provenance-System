"""Unit tests for HybridSearch reference chunk demotion."""

import pytest
from src.core.query_engine.hybrid_search import HybridSearch, HybridSearchConfig
from src.core.types import RetrievalResult


def test_reference_chunks_demoted_in_results():
    """Reference chunks should have their scores multiplied by reference_weight."""
    config = HybridSearchConfig(
        enable_dense=False,
        enable_sparse=False,
        fusion_top_k=10,
        reference_weight=0.3,
    )
    hybrid = HybridSearch(config=config)

    results = [
        RetrievalResult(
            chunk_id="body_1",
            score=0.9,
            text="Important content about deep learning.",
            metadata={"chunk_type": "body"},
        ),
        RetrievalResult(
            chunk_id="ref_1",
            score=0.85,
            text="[1] Smith et al. Deep learning survey. Nature, 2023.",
            metadata={"chunk_type": "reference"},
        ),
        RetrievalResult(
            chunk_id="body_2",
            score=0.7,
            text="Another relevant paragraph.",
            metadata={},
        ),
    ]

    demoted = hybrid._apply_reference_demotion(results)

    # ref_1 score should be 0.85 * 0.3 = 0.255
    assert demoted[-1].chunk_id == "ref_1", "Reference chunk should rank last after demotion"
    assert demoted[-1].score == pytest.approx(0.255, rel=0.01)

    # body chunks should keep original scores
    body_scores = [r.score for r in demoted if r.chunk_id == "body_1"]
    assert body_scores[0] == 0.9


def test_reference_weight_1_0_no_demotion():
    """reference_weight=1.0 should not change any scores."""
    config = HybridSearchConfig(
        enable_dense=False,
        enable_sparse=False,
        reference_weight=1.0,
    )
    hybrid = HybridSearch(config=config)

    results = [
        RetrievalResult(
            chunk_id="ref_1",
            score=0.8,
            text="[1] A reference.",
            metadata={"chunk_type": "reference"},
        ),
        RetrievalResult(
            chunk_id="body_1",
            score=0.5,
            text="Body text.",
            metadata={},
        ),
    ]

    demoted = hybrid._apply_reference_demotion(results)
    assert demoted[0].score == 0.8, "Score should be unchanged when weight=1.0"
    assert demoted[0].chunk_id == "ref_1"


def test_reference_demotion_empty_results():
    """Empty results should not cause errors."""
    config = HybridSearchConfig(reference_weight=0.3)
    hybrid = HybridSearch(config=config)
    result = hybrid._apply_reference_demotion([])
    assert result == []


def test_reference_demotion_no_reference_chunks():
    """Results without reference chunks should be unchanged."""
    config = HybridSearchConfig(reference_weight=0.3)
    hybrid = HybridSearch(config=config)

    results = [
        RetrievalResult(chunk_id="a", score=0.9, text="Body A.", metadata={}),
        RetrievalResult(chunk_id="b", score=0.5, text="Body B.", metadata={"chunk_type": "body"}),
    ]

    demoted = hybrid._apply_reference_demotion(results)
    assert demoted[0].score == 0.9
    assert demoted[1].score == 0.5
