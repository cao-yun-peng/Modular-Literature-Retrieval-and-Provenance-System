"""Human mappings cannot bypass provenance, truncation or version checks."""

import copy

import pytest

from src.observability.evaluation.benchmark_data import anchor, digest, index_identity, map_evidence
from src.observability.evaluation.benchmark_mapping import mapped_cases
from src.observability.evaluation.benchmark_scoring import coverage, expanded


def test_verified_mapping_survives_format_change_but_not_truncation():
    source = "The nonreciprocal interaction changes the measured speed at 100 micrometers."
    parsed = source.replace("nonreciprocal", "non-reciprocal")
    span = anchor("p", 1, source, source)
    case = {
        "id": "q",
        "answerable": True,
        "evidence_groups": [{"id": "g", "alternatives": [[span]]}],
    }
    data = {"corpus_id": "corpus", "test_cases": [case]}
    chunks = [{"chunk_id": "c", "paper_id": "p", "text": parsed}]
    index = {"corpus_id": "corpus", "chunks": chunks, "index_id": index_identity(chunks)}
    mapping = map_evidence(data, index)
    assert coverage(mapped_cases(data, index, mapping)[0], chunks)[0] == 0
    row = mapping["cases"]["q"][0]
    row.update(
        review_status="approved",
        reviewer="human-fixture",
        reviewed_at="2026-09-09",
        approved_alternatives=[[{"chunk_id": "c", "start": 0, "end": len(parsed)}]],
    )
    verified = mapped_cases(data, index, mapping)[0]
    assert coverage(verified, chunks)[0] == 1
    assert coverage(verified, expanded(chunks, 30))[0] == 0
    assert "_mapped_spans" not in case
    row["reviewer"] = None
    with pytest.raises(ValueError, match="human review"):
        mapped_cases(data, index, mapping)


def test_mapping_rejects_stale_version_and_foreign_paper():
    source = "This is a verified source statement with a physical measurement of 100 units."
    span = anchor("p", 1, source, source)
    case = {"id": "q", "evidence_groups": [{"id": "g", "alternatives": [[span]]}]}
    data = {"test_cases": [case]}
    index = {
        "index_id": "index",
        "chunks": [{"chunk_id": "c", "paper_id": "another", "text": source}],
    }
    mapping = {
        "dataset_id": digest(data),
        "index_id": "index",
        "cases": {
            "q": [
                {
                    "span_id": digest(span),
                    "review_status": "approved",
                    "reviewer": "fixture",
                    "reviewed_at": "2026-09-09",
                    "approved_alternatives": [[{"chunk_id": "c", "start": 0, "end": len(source)}]],
                }
            ]
        },
    }
    with pytest.raises(ValueError, match="another source paper"):
        mapped_cases(data, index, mapping)
    wrong = copy.deepcopy(mapping)
    wrong["index_id"] = "stale"
    with pytest.raises(ValueError, match="another dataset/index"):
        mapped_cases(data, index, wrong)
