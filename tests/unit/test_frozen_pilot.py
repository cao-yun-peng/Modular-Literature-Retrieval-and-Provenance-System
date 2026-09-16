"""Acceptance tests for source identity, coverage semantics and review gates."""

import copy
import json

import pytest

from src.observability.evaluation.frozen_pilot import (
    SCHEMA,
    FrozenCorpus,
    import_reviews,
    read,
    retrieve,
    score_case,
    score_run,
    sha,
    validate,
    write,
)
from src.observability.evaluation.frozen_pilot_review import export_review

Q1 = "Exact evidence from the first paper explains the experimental condition."
Q2 = "Independent evidence from a second paper describes a different mechanism."


@pytest.fixture
def pilot(tmp_path):
    root = tmp_path / "baseline"
    root.mkdir()
    chunks = [
        {"id": "c1", "document_id": "d1", "text": Q1},
        {"id": "c2", "document_id": "d2", "text": Q2},
        {"id": "c3", "document_id": "d2", "text": Q1},  # Wrong paper, identical words.
        {"id": "c4", "document_id": "d1", "text": "Before " + Q1 + " After"},
    ]
    documents = [{"document_id": d, "sha256": d, "title": d} for d in ("d1", "d2")]
    embedding = {
        "provider": "dashscope",
        "model": "text-embedding-v3",
        "dimensions": 2,
        "base_url": "https://example.invalid",
    }
    files = {
        "documents.json": documents,
        "chunks.json": chunks,
        "settings.json": {"embedding": embedding},
        "vectors.json": [
            {"id": c["id"], "embedding": v}
            for c, v in zip(chunks, ([1, 0], [0, 1], [-1, 0], [1, 0]))
        ],
        "bm25.json": {
            "metadata": {"avg_doc_length": 5},
            "index": {
                "evidence": {"idf": 1.0, "postings": [{"chunk_id": "c1", "tf": 1, "doc_length": 5}]}
            },
        },
    }
    for name, data in files.items():
        (root / name).write_text(json.dumps(data), encoding="utf-8")
    manifest = {
        "status": "frozen",
        "baseline_id": "fixture",
        "collection": "test",
        "chunk_count": 4,
        "embedding_dimensions": 2,
        "files": {name: sha((root / name).read_bytes()) for name in files},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    corpus = FrozenCorpus(root)
    span1 = {"document_id": "d1", "chunk_id": "c1", "start": 0, "end": len(Q1), "quote": Q1}
    span2 = {"document_id": "d2", "chunk_id": "c2", "start": 0, "end": len(Q2), "quote": Q2}
    groups = [{"id": "g1", "alternatives": [[span1]]}, {"id": "g2", "alternatives": [[span2]]}]
    cases = [
        {
            "id": f"q{i}",
            "query": f"evidence question {i}",
            "reference_answer": "answer",
            "language": "en",
            "split": "dev",
            "answerable": True,
            "query_type": "cross_paper",
            "evidence_groups": copy.deepcopy(groups),
            "review": {"status": "pending"},
        }
        for i in range(20)
    ]
    dataset = {"schema_version": SCHEMA, "baseline": corpus.identity(), "cases": cases}
    return corpus, dataset


def test_cross_paper_coverage_and_wrong_paper_quote(pilot):
    corpus, dataset = pilot
    case = dataset["cases"][0]
    first = score_case(case, ["c3", "c1", "c2"], corpus, 2)
    assert first["evidence_recall"] == 0.5
    assert first["all_evidence"] == 0
    assert first["target_rr"] == 0.5
    assert first["document_hit"] == 1
    assert first["target_document_recall"] == 1
    complete = score_case(case, ["c3", "c1", "c2"], corpus, 3)
    assert complete["all_evidence"] == 1
    assert complete["mrr"] is None and complete["ndcg"] is None


def test_overlap_duplicate_empty_and_multiple_span_plan(pilot):
    corpus, dataset = pilot
    case = dataset["cases"][0]
    assert score_case(case, ["c4"], corpus, 1)["evidence_recall"] == 0.5
    result = score_case(case, ["c1", "c1", "c2"], corpus, 2)
    assert result["duplicate_rate"] == 0.5 and result["evidence_recall"] == 0.5
    assert score_case(case, [], corpus, 10)["target_rr"] == 0
    case["evidence_groups"] = [
        {"id": "both", "alternatives": [[g["alternatives"][0][0] for g in case["evidence_groups"]]]}
    ]
    assert score_case(case, ["c1"], corpus, 10)["evidence_recall"] == 0
    assert score_case(case, ["c1", "c1", "c2"], corpus, 10)["target_rr"] == pytest.approx(1 / 3)


def test_offsets_baseline_and_review_gate(pilot):
    corpus, dataset = pilot
    assert validate(dataset, corpus, allow_draft=True)["cases"] == 20
    with pytest.raises(ValueError, match="Human question review"):
        validate(dataset, corpus)
    tampered = copy.deepcopy(dataset)
    tampered["cases"][0]["evidence_groups"][0]["alternatives"][0][0]["start"] = 1
    with pytest.raises(ValueError, match="quote mismatch"):
        validate(tampered, corpus, allow_draft=True)
    (corpus.root / "chunks.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        FrozenCorpus(corpus.root)


@pytest.mark.parametrize("bad_id", ["unknown", "../c1"])
def test_unknown_ids_fail_instead_of_scoring_as_misses(pilot, bad_id):
    corpus, dataset = pilot
    with pytest.raises(ValueError, match="unknown chunk"):
        score_case(dataset["cases"][0], [bad_id], corpus, 10)


def test_query_only_retrieval_and_deterministic_replay(pilot):
    corpus, dataset = pilot
    run = retrieve(dataset, corpus)
    changed = copy.deepcopy(dataset)
    changed["cases"][0]["reference_answer"] = "INJECTED ANSWER evidence"
    changed["cases"][0]["evidence_groups"] = []
    other = retrieve(changed, corpus)
    assert [r["chunk_ids"] for r in run["results"]] == [r["chunk_ids"] for r in other["results"]]
    report = score_run(dataset, run, corpus, allow_draft=True)
    replay = score_run(dataset, json.loads(json.dumps(run)), corpus, allow_draft=True)
    assert report["aggregate"] == replay["aggregate"]
    assert report["status"] == "provisional_unreviewed"
    assert report["aggregate"]["10"]["evidence_recall"] == 0.5


@pytest.mark.parametrize(
    "mutation", ["dataset", "query", "missing", "duplicate", "failure", "depth"]
)
def test_bad_run_cannot_produce_a_score(pilot, mutation):
    corpus, dataset = pilot
    run = retrieve(dataset, corpus)
    if mutation == "dataset":
        run["dataset_sha256"] = "wrong"
    elif mutation == "query":
        run["results"][0]["query"] = "wrong"
    elif mutation == "missing":
        run["results"].pop()
    elif mutation == "duplicate":
        run["results"][0] = run["results"][1]
    elif mutation == "failure":
        run["results"][0]["status"] = "error"
    else:
        run["top_k"] = 1
    with pytest.raises(ValueError):
        score_run(dataset, run, corpus, allow_draft=True)


def test_review_import_is_version_bound_and_requires_source_confirmation(pilot):
    _, dataset = pilot
    rows = [
        {
            "case_id": c["id"],
            "status": "approved",
            "reviewer": "human",
            "reviewed_at": "2026-09-16T00:00:00Z",
            "source_checked": True,
        }
        for c in dataset["cases"]
    ]
    export = {"dataset_sha256": sha(dataset), "reviews": rows}
    result = import_reviews(dataset, export)
    assert result["cases"][0]["review"]["status"] == "approved"
    assert dataset["cases"][0]["review"]["status"] == "pending"
    rows[0]["source_checked"] = False
    with pytest.raises(ValueError, match="Source"):
        import_reviews(dataset, export)
    export["dataset_sha256"] = "stale"
    with pytest.raises(ValueError, match="another dataset"):
        import_reviews(dataset, export)


def test_dense_and_rrf_share_snapshot_with_query_cache_identity(pilot):
    corpus, dataset = pilot
    queries = [{"case_id": c["id"], "query": c["query"]} for c in dataset["cases"]]
    cache = {
        "baseline": corpus.identity(),
        "queries_sha256": sha(queries),
        "embedding": corpus.checked("settings.json")["embedding"],
        "vectors": [{"case_id": c["id"], "embedding": [1, 0]} for c in dataset["cases"]],
    }
    dense = retrieve(dataset, corpus, strategy="dense", query_vectors=cache)
    assert dense["results"][0]["chunk_ids"] == ["c1", "c4", "c2", "c3"]
    hybrid = retrieve(dataset, corpus, strategy="hybrid", query_vectors=cache)
    assert hybrid["results"][0]["chunk_ids"][0] == "c1"
    cache["embedding"]["model"] = "wrong"
    with pytest.raises(ValueError, match="model"):
        retrieve(dataset, corpus, strategy="dense", query_vectors=cache)


def test_review_html_escapes_untrusted_source_and_protects_baseline(pilot, tmp_path):
    corpus, dataset = pilot
    case = dataset["cases"][0]
    case["query"] = "</script><script>alert('bad')</script>"
    for group in case["evidence_groups"]:
        group["purpose"] = "test"
    output = tmp_path / "review.html"
    export_review(dataset, corpus, output)
    assert "</script><script>alert" not in output.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        write(corpus.root / "new.json", {}, corpus.root)
    with pytest.raises(ValueError, match="outside"):
        export_review(dataset, corpus, corpus.root / "review.html")
    write(tmp_path / "output.json", {"ok": True}, corpus.root)
    assert read(tmp_path / "output.json") == {"ok": True}
