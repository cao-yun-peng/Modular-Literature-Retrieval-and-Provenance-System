"""Read-only, source-anchored retrieval pilot for an immutable paper snapshot.

Gold snippets are known supporting evidence, not exhaustive relevance judgments.
Consequently this evaluator reports TargetRR and evidence-group recall, and leaves
ordinary MRR/nDCG unset. It never sends answers or gold evidence to retrieval.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "frozen-paper-pilot/1.0"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value, baseline):
    path = Path(path).resolve()
    if path.is_relative_to(Path(baseline).resolve()):
        raise ValueError("Evaluation output must be outside the frozen baseline")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize(text):
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return " ".join(text.split())


class FrozenCorpus:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = read(self.root / "manifest.json")
        if self.manifest["status"] != "frozen":
            raise ValueError("Corpus is not frozen")
        self.fingerprint = sha((self.root / "manifest.json").read_bytes())
        self.documents = {d["document_id"]: d for d in self.checked("documents.json")}
        rows = self.checked("chunks.json")
        self.chunks = {c["id"]: c for c in rows}
        if len(rows) != len(self.chunks) or len(rows) != self.manifest["chunk_count"]:
            raise ValueError("Chunk count/identity mismatch")
        self.pages = {}

    def checked(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Snapshot path escapes baseline")
        data = path.read_bytes()
        if sha(data) != self.manifest["files"].get(name):
            raise ValueError(f"Snapshot hash mismatch: {name}")
        return json.loads(data)

    def source_pages(self, document_id):
        import fitz

        if document_id not in self.pages:
            name = f"pdfs/{self.documents[document_id]['sha256']}.pdf"
            data = (self.root / name).read_bytes()
            if sha(data) != self.manifest["files"][name]:
                raise ValueError(f"PDF hash mismatch: {document_id}")
            with fitz.open(stream=data, filetype="pdf") as pdf:
                self.pages[document_id] = [normalize(page.get_text()) for page in pdf]
        return self.pages[document_id]

    def identity(self):
        return {
            "baseline_id": self.manifest["baseline_id"],
            "manifest_sha256": self.fingerprint,
            "collection": self.manifest["collection"],
            "chunk_count": len(self.chunks),
            "document_count": len(self.documents),
        }


def validate(dataset, corpus, *, allow_draft=False):
    if dataset.get("schema_version") != SCHEMA or dataset.get("baseline") != corpus.identity():
        raise ValueError("Dataset schema or baseline fingerprint mismatch")
    cases = dataset["cases"]
    if len(cases) != 20 or len({c["id"] for c in cases}) != 20:
        raise ValueError("Pilot requires exactly 20 unique cases")
    if len({normalize(c["query"]) for c in cases}) != 20:
        raise ValueError("Duplicate queries")
    spans = 0
    source_verified = 0
    for case in cases:
        if not case["query"].strip() or not case["reference_answer"].strip():
            raise ValueError(f"Empty question/answer: {case['id']}")
        if case.get("split") != "dev" or case.get("answerable") is not True:
            raise ValueError("This pilot supports answerable development questions only")
        review = case.get("review", {})
        approved = review.get("status") == "approved"
        if approved and not all(
            review.get(k) for k in ("reviewer", "reviewed_at", "source_checked")
        ):
            raise ValueError("Approval requires reviewer, timestamp and source confirmation")
        if not allow_draft and not approved:
            raise ValueError("Human question review pending; --allow-draft gives diagnostics only")
        groups = case["evidence_groups"]
        if not groups or len({g["id"] for g in groups}) != len(groups):
            raise ValueError("Missing/duplicate evidence groups")
        papers = set()
        for group in groups:
            if not group["alternatives"]:
                raise ValueError("Empty evidence alternatives")
            for alternative in group["alternatives"]:
                if not alternative:
                    raise ValueError("Empty evidence plan")
                for span in alternative:
                    chunk = corpus.chunks.get(span["chunk_id"])
                    if not chunk or chunk["document_id"] != span["document_id"]:
                        raise ValueError("Evidence identity mismatch")
                    start, end = span["start"], span["end"]
                    if not (
                        type(start) is int
                        and type(end) is int
                        and 0 <= start < end <= len(chunk["text"])
                    ):
                        raise ValueError("Invalid evidence offsets")
                    if end - start < 30 or chunk["text"][start:end] != span["quote"]:
                        raise ValueError("Evidence quote mismatch")
                    papers.add(chunk["document_id"])
                    spans += 1
                    source = span.get("pdf_source")
                    if source:
                        pages = corpus.source_pages(span["document_id"])
                        page, quote = source["physical_page"], source["quote"]
                        if type(page) is not int or not 1 <= page <= len(pages):
                            raise ValueError("Invalid physical PDF page")
                        if len(quote) < 30 or quote not in pages[page - 1]:
                            raise ValueError("PDF source quote mismatch")
                        source_verified += 1
        if case["query_type"] == "cross_paper" and (len(papers) < 2 or len(groups) < 2):
            raise ValueError("Cross-paper question must require multiple papers/groups")
    return {
        "status": "structurally_valid",
        "cases": len(cases),
        "languages": dict(Counter(c["language"] for c in cases)),
        "types": dict(Counter(c["query_type"] for c in cases)),
        "approved_cases": sum(c.get("review", {}).get("status") == "approved" for c in cases),
        "evidence_spans": spans,
        "pdf_source_spans_verified": source_verified,
        "meaning": "Exact source/offset validation is not human semantic review",
    }


def _span_hit(span, ids, corpus):
    # Accept the same verbatim evidence in another overlapping chunk of the same
    # paper. No substring from another paper or fuzzy similarity earns credit.
    quote = normalize(span["quote"])
    return any(
        corpus.chunks[cid]["document_id"] == span["document_id"]
        and quote in normalize(corpus.chunks[cid]["text"])
        for cid in ids
    )


def score_case(case, ranking, corpus, k):
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    if any(cid not in corpus.chunks for cid in ranking):
        raise ValueError("Run contains an unknown chunk ID")
    # Duplicates occupy their original ranks, but cannot add coverage.
    ids = ranking[:k]
    groups = case["evidence_groups"]

    def covered(group, prefix):
        return any(
            all(_span_hit(s, prefix, corpus) for s in plan) for plan in group["alternatives"]
        )

    hits = [g["id"] for g in groups if covered(g, ids)]
    targets = {s["document_id"] for g in groups for a in g["alternatives"] for s in a}
    retrieved_docs = {corpus.chunks[cid]["document_id"] for cid in ids}
    first = next(
        (i for i in range(1, len(ids) + 1) if any(covered(g, ids[:i]) for g in groups)), None
    )
    return {
        "document_hit": float(bool(targets & retrieved_docs)),
        "target_document_recall": len(targets & retrieved_docs) / len(targets),
        "evidence_recall": len(hits) / len(groups),
        "all_evidence": float(len(hits) == len(groups)),
        "target_rr": 1 / first if first else 0.0,
        "duplicate_rate": 1 - len(set(ids)) / len(ids) if ids else 0.0,
        "covered_groups": hits,
        "mrr": None,
        "ndcg": None,
        "ranking_judgment_state": "not_exhaustively_judged",
    }


def score_run(dataset, run, corpus, *, ks=(5, 10), allow_draft=False):
    validation = validate(dataset, corpus, allow_draft=allow_draft)
    if run.get("baseline") != corpus.identity() or run.get("dataset_sha256") != sha(dataset):
        raise ValueError("Run belongs to another baseline/dataset version")
    rows = run["results"]
    if len(rows) != len(dataset["cases"]) or {r["case_id"] for r in rows} != {
        c["id"] for c in dataset["cases"]
    }:
        raise ValueError("Run must contain every case exactly once")
    by_id = {r["case_id"]: r for r in rows}
    scored = []
    for case in dataset["cases"]:
        row = by_id[case["id"]]
        if row.get("status") != "ok":
            raise ValueError(f"Retrieval failed for {case['id']}; errors are not empty results")
        if row.get("query") != case["query"]:
            raise ValueError("Run query differs from dataset")
        if run.get("top_k", 0) < max(ks):
            raise ValueError("Run retrieval depth is smaller than evaluation cutoff")
        scored.append(
            {
                "case_id": case["id"],
                "language": case["language"],
                "query_type": case["query_type"],
                "scores": {str(k): score_case(case, row["chunk_ids"], corpus, k) for k in ks},
            }
        )

    def aggregate(subset):
        return {
            str(k): {
                name: sum(r["scores"][str(k)][name] for r in subset) / len(subset)
                for name in (
                    "document_hit",
                    "target_document_recall",
                    "evidence_recall",
                    "all_evidence",
                    "target_rr",
                    "duplicate_rate",
                )
            }
            for k in ks
        }

    return {
        "schema_version": SCHEMA,
        "created_at": now(),
        "baseline": corpus.identity(),
        "dataset_sha256": sha(dataset),
        "run_sha256": sha(run),
        "strategy": run["strategy"],
        "status": "reviewed_pilot"
        if validation["approved_cases"] == 20
        else "provisional_unreviewed",
        "validation": validation,
        "aggregate": aggregate(scored),
        "by_language": {
            x: aggregate([r for r in scored if r["language"] == x])
            for x in sorted({r["language"] for r in scored})
        },
        "by_type": {
            x: aggregate([r for r in scored if r["query_type"] == x])
            for x in sorted({r["query_type"] for r in scored})
        },
        "cases": scored,
        "limitations": [
            "20 source-authored development cases; no held-out generalization claim",
            "Evidence recall covers annotated required groups, not all relevant chunks",
            "TargetRR is the first complete annotated evidence group, not ordinary MRR",
            "MRR/nDCG unavailable without a reviewed relevance pool",
            "No generated-answer quality, unanswerable, handoff or MCP/service evaluation",
            "Whole-chunk retrieval; no equal-token context budget; no causal ablation claim",
        ],
    }


def retrieve(dataset, corpus, *, strategy="bm25", top_k=10, query_vectors=None):
    """Query the snapshot exports, without opening or modifying live databases."""
    from src.core.query_engine.fusion import RRFFusion
    from src.core.query_engine.query_processor import QueryProcessor
    from src.core.types import RetrievalResult
    from src.ingestion.storage.bm25_indexer import BM25Indexer

    if strategy not in ("bm25", "dense", "hybrid") or not 1 <= top_k <= 20:
        raise ValueError("Supported strategies: bm25/dense/hybrid; depth 1..20")
    sparse = corpus.checked("bm25.json")
    bm25 = BM25Indexer(index_dir=str(corpus.root))
    bm25._metadata, bm25._index = sparse["metadata"], sparse["index"]
    processor = QueryProcessor()
    matrix, vector_ids, qvectors = None, [], {}
    if strategy != "bm25":
        import numpy as np

        if not query_vectors or query_vectors["baseline"] != corpus.identity():
            raise ValueError("Matching cached query embeddings are required")
        expected_queries = [{"case_id": c["id"], "query": c["query"]} for c in dataset["cases"]]
        if query_vectors["queries_sha256"] != sha(expected_queries):
            raise ValueError("Query embedding cache does not match questions")
        embedding_settings = corpus.checked("settings.json")["embedding"]
        expected = {
            k: embedding_settings[k] for k in ("provider", "model", "dimensions", "base_url")
        }
        if query_vectors.get("embedding") != expected:
            raise ValueError("Query embedding model does not match frozen document embeddings")
        vectors = corpus.checked("vectors.json")
        vector_ids = [v["id"] for v in vectors]
        if len(vector_ids) != len(set(vector_ids)) or set(vector_ids) != set(corpus.chunks):
            raise ValueError("Vector/chunk identity mismatch")
        matrix = np.asarray([v["embedding"] for v in vectors], dtype=float)
        dim = corpus.manifest["embedding_dimensions"]
        if (
            matrix.shape != (len(vector_ids), dim)
            or not np.isfinite(matrix).all()
            or (np.linalg.norm(matrix, axis=1) == 0).any()
        ):
            raise ValueError("Invalid document vectors")
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        qvectors = {r["case_id"]: r["embedding"] for r in query_vectors["vectors"]}
        if len(qvectors) != len(query_vectors["vectors"]) or set(qvectors) != {
            c["id"] for c in dataset["cases"]
        }:
            raise ValueError("Missing/duplicate query vectors")

    def packed(rows):
        return [
            RetrievalResult(
                chunk_id=r["chunk_id"],
                score=r["score"],
                text=corpus.chunks[r["chunk_id"]]["text"],
                metadata={},
            )
            for r in rows
        ]

    results = []
    for case in dataset["cases"]:
        started = time.perf_counter()
        query = case["query"]  # The only question data passed to the retriever.
        keywords = processor.process(query).keywords
        sparse_rows = bm25.query(keywords, top_k=20) if keywords else []
        dense_rows = []
        if strategy != "bm25":
            q = np.asarray(qvectors[case["id"]], dtype=float)
            if q.shape != (matrix.shape[1],) or not np.isfinite(q).all() or np.linalg.norm(q) == 0:
                raise ValueError("Invalid query embedding")
            scores = matrix @ (q / np.linalg.norm(q))
            dense_rows = sorted(
                [{"chunk_id": cid, "score": float(s)} for cid, s in zip(vector_ids, scores)],
                key=lambda r: (-r["score"], r["chunk_id"]),
            )[:20]
        if strategy == "hybrid":
            found = RRFFusion(k=60).fuse([packed(dense_rows), packed(sparse_rows)], top_k=top_k)
            found = [{"chunk_id": r.chunk_id, "score": r.score} for r in found]
        else:
            found = (sparse_rows if strategy == "bm25" else dense_rows)[:top_k]
        results.append(
            {
                "case_id": case["id"],
                "query": query,
                "status": "ok",
                "keywords": keywords,
                "chunk_ids": [r["chunk_id"] for r in found],
                "scores": [r["score"] for r in found],
                "local_retrieval_ms": (time.perf_counter() - started) * 1000,
            }
        )
    sources = [
        Path(__file__),
        Path("src/core/query_engine/query_processor.py"),
        Path("src/ingestion/storage/bm25_indexer.py"),
        Path("src/core/query_engine/fusion.py"),
    ]
    return {
        "schema_version": SCHEMA,
        "created_at": now(),
        "baseline": corpus.identity(),
        "dataset_sha256": sha(dataset),
        "strategy": strategy,
        "top_k": top_k,
        "runtime": {
            "python": platform.python_version(),
            "source_hashes": {str(p): sha(p.read_bytes()) for p in sources},
        },
        "settings": {
            "candidate_depth_per_route": 20,
            "rrf_k": 60,
            "bm25_k1": 1.5,
            "bm25_b": 0.75,
            "query_preprocessing": "project QueryProcessor, original casing, no translation",
            "dense": "exact cosine over frozen vectors; not live Chroma ANN",
            "reranker": "disabled",
        },
        "query_vectors_sha256": sha(query_vectors) if query_vectors else None,
        "timing_scope": "local retrieval only; excludes embedding API, loading and scoring",
        "results": results,
    }


def import_reviews(dataset, reviews):
    if reviews.get("dataset_sha256") != sha(dataset):
        raise ValueError("Review file belongs to another dataset version")
    updated = json.loads(json.dumps(dataset))
    by_id = {c["id"]: c for c in updated["cases"]}
    seen = set()
    for row in reviews["reviews"]:
        cid = row["case_id"]
        if cid not in by_id or cid in seen:
            raise ValueError("Unknown/duplicate reviewed case")
        seen.add(cid)
        if row["status"] not in ("pending", "approved", "rejected"):
            raise ValueError("Invalid review status")
        if row["status"] != "pending" and not all(row.get(k) for k in ("reviewer", "reviewed_at")):
            raise ValueError("Review identity/timestamp missing")
        if row["status"] == "approved" and row.get("source_checked") is not True:
            raise ValueError("Source must be checked before approval")
        by_id[cid]["review"] = {k: v for k, v in row.items() if k != "case_id"}
    updated["parent_dataset_sha256"] = sha(dataset)
    updated["review_imported_at"] = now()
    return updated
