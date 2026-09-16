"""Hand-computable metrics and lifecycle gates, with no external services."""

from __future__ import annotations

import copy
import json
import math

import pytest

from src.observability.evaluation.benchmark_data import (
    SCHEMA,
    anchor,
    digest,
    index_identity,
    map_evidence,
    validate_dataset,
)
from src.observability.evaluation.benchmark_runner import (
    collect_usage,
    replay,
    run_benchmark,
    validate_judgments,
)
from src.observability.evaluation.benchmark_scoring import (
    aggregate,
    coverage,
    expanded,
    paired_bootstrap,
    ranking,
    score_case,
    span_covered,
)

TEXT = "The positive defect moves faster under nonreciprocal interactions while the negative defect remains slow."
OTHER = "The second paper reports a distinct mechanism driven by fluid flow and confinement."


@pytest.fixture
def fixture():
    pages = {
        "p1": [{"page": 1, "text": TEXT}, {"page": 2, "text": OTHER}],
        "p2": [{"page": 1, "text": OTHER}],
    }
    corpus = {
        "corpus_id": "corpus",
        "family_review": {"status": "pending"},
        "papers": [
            {
                "paper_id": "p1",
                "family_id": "f1",
                "split": "dev",
                "topics": ["physics"],
                "status": "parsed",
            },
            {
                "paper_id": "p2",
                "family_id": "f2",
                "split": "dev",
                "topics": ["physics"],
                "status": "parsed",
            },
        ],
    }
    case = {
        "id": "q1",
        "query": "Which defect travels faster?",
        "language": "en",
        "split": "dev",
        "query_type": "concept",
        "answerable": True,
        "source_paper_ids": ["p1"],
        "evidence_groups": [{"id": "g1", "alternatives": [[anchor("p1", 1, TEXT, TEXT)]]}],
        "review": {"status": "pending"},
        "query_params": {},
    }
    data = {
        "schema_version": SCHEMA,
        "corpus_id": "corpus",
        "status": "draft",
        "test_cases": [case],
    }
    chunks = [
        {"chunk_id": "c1", "paper_id": "p1", "text": TEXT, "metadata": {}},
        {"chunk_id": "c2", "paper_id": "p2", "text": OTHER, "metadata": {}},
    ]
    index = {
        "corpus_id": "corpus",
        "collection": "benchmark",
        "index_id": index_identity(chunks),
        "chunks": chunks,
    }
    return data, corpus, pages, index


def judged(grade):
    return {"grade": grade, "reviewer": "human-fixture", "reviewed_at": "2026-09-09"}


def test_span_can_cross_chunks_without_losing_a_number():
    quote = "This experimentally observed transition occurs precisely at 100 micrometers of confinement."
    s = anchor("p", 1, quote, quote)
    assert span_covered(
        s, [{"paper_id": "p", "text": quote[:55]}, {"paper_id": "p", "text": quote[40:]}]
    )
    assert not span_covered(s, [{"paper_id": "p", "text": quote.replace("100", "200")}])
    assert not span_covered(s, [{"paper_id": "another", "text": quote}])


def test_alternatives_and_cross_paper_requirements(fixture):
    data, _, pages, index = fixture
    c = copy.deepcopy(data["test_cases"][0])
    s2 = anchor("p2", 1, OTHER, OTHER)
    c["evidence_groups"][0]["alternatives"].append([s2])
    assert coverage(c, [index["chunks"][1]])[0] == 1
    c["evidence_groups"].append({"id": "g2", "alternatives": [[s2]]})
    assert coverage(c, [index["chunks"][0]])[0] == 0.5
    assert coverage(c, index["chunks"])[0] == 1
    assert coverage(c, [index["chunks"][0]] * 3)[0] == 0.5


def test_graded_ndcg_hand_calculation_and_unjudged():
    entries = [{"chunk_id": "b"}, {"chunk_id": "a"}, {"chunk_id": "a"}]
    j = {"a": judged(2), "b": judged(1)}
    r = ranking(entries, j, 3)
    assert r["mrr_at_k"] == 1.0
    assert r["direct_mrr_at_k"] == 0.5
    assert r["ndcg_at_k"] == pytest.approx((1 + 2 / math.log2(3)) / (2 + 1 / math.log2(3)))
    r = ranking(entries, {"a": judged(2)}, 3)
    assert r["ndcg_at_k"] is None
    assert r["judged_at_k"] == pytest.approx(2 / 3)


def test_unknown_wrong_and_correct_page_locations(fixture):
    data, _, pages, index = fixture
    item = dict(index["chunks"][0], page_start=1, page_end=1)
    c = data["test_cases"][0]

    def score(e):
        return score_case(c, {"evidence": [e]}, pages, {"c1": judged(2)}, index["chunks"], 5)[
            "metrics"
        ]

    assert score(item)["citation_correctness"] == 1
    assert score(dict(item, page_start=2, page_end=2))["page_accuracy"] == 0
    missing = score(dict(item, page_start=None, page_end=None))
    assert missing["page_accuracy"] is None
    assert missing["page_completeness"] == 0
    assert score(dict(item, text="Invented unsupported text"))["source_traceability"] == 0


def test_expansion_budget_and_negative_na(fixture):
    data, _, pages, index = fixture
    c = copy.deepcopy(data["test_cases"][0])
    item = dict(index["chunks"][0], text="A short introduction.", expanded_context={"text": TEXT})
    r = score_case(c, {"evidence": [item]}, pages, {}, index["chunks"], 5, budget=10)
    assert r["metrics"]["evidence_recall_at_k"] == 0
    assert r["metrics"]["expanded_evidence_recall"] == 1
    assert r["metrics"]["budgeted_evidence_recall"] == 0
    assert sum(len(x["text"]) for x in expanded([item], 10)) == 10
    c.update(answerable=False, evidence_groups=[], query_type="unanswerable")
    r = score_case(c, {"evidence": []}, pages, {}, index["chunks"], 5)
    assert r["metrics"]["mrr_at_k"] is None
    assert r["metrics"]["evidence_recall_at_k"] is None
    assert r["coverage_signal"] == "not_evaluated"


def test_dataset_quote_leakage_and_review_gates(fixture):
    data, corpus, pages, _ = fixture
    validate_dataset(data, corpus, pages)
    with pytest.raises(ValueError, match="Human review"):
        validate_dataset(data, corpus, pages, freeze=True)
    corpus["papers"][0]["split"] = "test"
    with pytest.raises(ValueError, match="leaks"):
        validate_dataset(data, corpus, pages)
    corpus["papers"][0]["split"] = "dev"
    data["test_cases"][0]["evidence_groups"][0]["alternatives"][0][0]["quote"] = "wrong"
    with pytest.raises(ValueError, match="Quote/offset"):
        validate_dataset(data, corpus, pages)


def test_index_mapping_not_a_human_judgment(fixture):
    data, _, _, index = fixture
    mapping = map_evidence(data, index)
    row = mapping["cases"]["q1"][0]
    assert row["exact_candidates"] == ["c1"]
    assert row["review_status"] == "pending"
    index["chunks"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="identity"):
        map_evidence(data, index)


def test_judgments_bound_to_dataset_and_index(fixture):
    data, _, _, index = fixture
    with pytest.raises(ValueError, match="another"):
        validate_judgments({"index_id": "old", "dataset_id": digest(data)}, index, data)


class Adapter:
    def __init__(self, index, error=False):
        self.index, self.error, self.calls = index, error, []

    def query(self, case, k, strategy):
        self.calls.append((case["id"], k, strategy))
        if self.error:
            raise RuntimeError("Unavailable index")
        return {
            "evidence": [dict(self.index["chunks"][0], page_start=1, page_end=1)],
            "fallback": False,
        }


def test_runner_real_calls_k_separation_and_offline_replay(fixture, tmp_path):
    data, corpus, pages, index = fixture
    adapter = Adapter(index)
    result = run_benchmark(data, corpus, pages, index, adapter, tmp_path)
    assert adapter.calls == [("q1", 5, "hybrid"), ("q1", 10, "hybrid")]
    assert result["status"] == "provisional"
    root = __import__("pathlib").Path(result["run_dir"])
    assert replay(root, corpus, pages, index) == json.loads((root / "summary.json").read_text())


def test_offline_label_migration_preserves_requests_and_run_immutability(fixture, tmp_path):
    data, corpus, pages, index = fixture
    result = run_benchmark(data, corpus, pages, index, Adapter(index), tmp_path)
    root = __import__("pathlib").Path(result["run_dir"])
    updated = copy.deepcopy(data)
    updated["version"] = "labels-2"
    labels = {
        "dataset_id": digest(updated),
        "index_id": index["index_id"],
        "cases": {"q1": {"c1": judged(2)}},
    }
    target = tmp_path / "replayed"
    replay(root, corpus, pages, index, judgments=labels, data=updated, output_run=target)
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["dataset_id"] == digest(updated)
    assert manifest["additional_model_calls"] == 0
    assert manifest["status"] == "provisional"
    row = json.loads((target / "query_results.jsonl").read_text().splitlines()[0])
    assert row["metrics"]["mrr_at_k"] == 1
    assert json.loads((root / "manifest.json").read_text())["dataset_id"] == digest(data)
    with pytest.raises(FileExistsError):
        replay(root, corpus, pages, index, judgments=labels, data=updated, output_run=target)
    updated["test_cases"][0]["query"] += " with a different constraint"
    with pytest.raises(ValueError, match="Query inputs changed"):
        replay(root, corpus, pages, index, data=updated)


def test_error_does_not_vanish_from_denominator(fixture, tmp_path):
    data, corpus, pages, index = fixture
    result = run_benchmark(
        data, corpus, pages, index, Adapter(index, error=True), tmp_path, ks=[10]
    )
    assert result["status"] == "failed"
    rows = [
        json.loads(line)
        for line in (__import__("pathlib").Path(result["run_dir"]) / "query_results.jsonl")
        .read_text()
        .splitlines()
    ]
    assert rows[0]["metrics"]["evidence_recall_at_k"] == 0
    assert aggregate(rows)["metrics"]["evidence_recall_at_k"]["denominator"] == 1
    assert aggregate(rows)["metrics"]["citation_correctness"]["denominator"] == 1
    assert aggregate(rows)["metrics"]["mrr_at_k"]["denominator"] == 1


def test_test_split_cannot_be_tuned_unreviewed(fixture, tmp_path):
    data, corpus, pages, index = fixture
    data["test_cases"][0]["split"] = "test"
    corpus["papers"][0]["split"] = "test"
    with pytest.raises(ValueError, match="Human review"):
        run_benchmark(data, corpus, pages, index, Adapter(index), tmp_path, split="test")


def test_forged_frozen_flag_cannot_make_development_run_formal(fixture, tmp_path):
    data, corpus, pages, index = fixture
    data["status"] = "frozen"
    with pytest.raises(ValueError, match="Human review"):
        run_benchmark(data, corpus, pages, index, Adapter(index), tmp_path)


def test_bootstrap_shared_papers_form_connected_groups():
    a = [
        {"case_id": "a", "family_ids": ["x"], "metrics": {"m": 0}},
        {"case_id": "b", "family_ids": ["x", "y"], "metrics": {"m": 1}},
        {"case_id": "c", "family_ids": ["z"], "metrics": {"m": 0}},
    ]
    b = [dict(r, metrics={"m": 1}) for r in a]
    result = paired_bootstrap(a, b, "m", samples=100)
    assert result["effective_groups"] == 2
    assert result["delta"] == pytest.approx(2 / 3)
    assert result == paired_bootstrap(a, b, "m", samples=100)
    with pytest.raises(ValueError, match="identical"):
        paired_bootstrap(a, b[:1], "m")


def test_usage_unknown_is_not_zero():
    assert collect_usage(None)["tokens"]["total_tokens"] is None
    trace = {"stages": [{"stage": "llm_usage", "data": {"usage": {"total_tokens": 14}}}]}
    assert collect_usage(trace)["tokens"]["total_tokens"] == 14


def test_full_freeze_quotas_pilot_and_review_records(fixture):
    from src.observability.evaluation.benchmark_data import QUOTAS

    data, corpus, pages, _ = fixture
    corpus["papers"] += [
        dict(p, paper_id=p["paper_id"] + "t", family_id=p["family_id"] + "t", split="test")
        for p in list(corpus["papers"])
    ]
    pages.update({p + "t": copy.deepcopy(rows) for p, rows in list(pages.items())})
    review = {
        "status": "approved",
        "reviewer": "human-fixture",
        "reviewed_at": "2026-09-09T00:00:00Z",
    }
    corpus["family_review"] = review
    seed = data["test_cases"][0]
    cases = []
    for split in ("dev", "test"):
        suffix = "t" if split == "test" else ""
        types = [t for t, n in QUOTAS.items() for _ in range(n // 2)]
        for i, kind in enumerate(types):
            c = copy.deepcopy(seed)
            c.update(
                id=f"{split}-{i}",
                query=digest(f"query-{split}-{i}"),
                split=split,
                query_type=kind,
                language="en" if i < 42 else "zh",
                pilot=split == "dev" and i < 20,
                review=review,
                second_review={**review, "blind": True},
                source_paper_ids=["p1" + suffix],
            )
            c["evidence_groups"][0]["alternatives"][0][0]["paper_id"] = "p1" + suffix
            if kind == "cross_paper":
                c["source_paper_ids"].append("p2" + suffix)
                c["evidence_groups"].append(
                    {"id": "g2", "alternatives": [[anchor("p2" + suffix, 1, OTHER, OTHER)]]}
                )
            if kind == "unanswerable":
                c.update(answerable=False, evidence_groups=[])
            cases.append(c)
    data.update(test_cases=cases, source_text_id=digest(pages))
    validate_dataset(data, corpus, pages, freeze=True)
    cases[0]["pilot"] = False
    with pytest.raises(ValueError, match="20 development"):
        validate_dataset(data, corpus, pages, freeze=True)
    cases[0]["pilot"] = True
    corpus["family_review"] = {"status": "approved"}
    with pytest.raises(ValueError, match="family"):
        validate_dataset(data, corpus, pages, freeze=True)


def test_unanswerable_relevance_and_boolean_grade_rejected(fixture):
    data, _, pages, index = fixture
    case = data["test_cases"][0]
    case.update(answerable=False, evidence_groups=[], query_type="unanswerable")
    reviewed = {"grade": 0, "reviewer": "fixture", "reviewed_at": "2026-09-09"}
    result = score_case(
        case, {"evidence": index["chunks"][:1]}, pages, {"c1": reviewed}, index["chunks"], 10
    )
    assert result["metrics"]["irrelevant_fraction"] == 1
    assert result["metrics"]["mrr_at_k"] is None
    with pytest.raises(ValueError, match="grade"):
        validate_judgments(
            {
                "dataset_id": digest(data),
                "index_id": index["index_id"],
                "cases": {"q1": {"c1": {**reviewed, "grade": True}}},
            },
            index,
            data,
        )
