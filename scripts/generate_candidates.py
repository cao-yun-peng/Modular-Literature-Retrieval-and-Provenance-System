#!/usr/bin/env python
"""Generate candidate chunk IDs for golden test set annotation.

For each query in the golden test set, runs HybridSearch and outputs
retrieved chunks with their IDs, scores, and text previews. The annotator
then selects which chunks are genuinely relevant and fills them into
``expected_chunk_ids``.

Usage::

    python scripts/generate_candidates.py --collection eval_test
    python scripts/generate_candidates.py --collection eval_test --top-k 20 --output candidates.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate candidate chunk IDs for golden test set annotation."
    )
    parser.add_argument(
        "--test-set",
        default="tests/fixtures/golden_test_set.json",
        help="Path to golden test set JSON (default: tests/fixtures/golden_test_set.json)",
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="Collection name to search within.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=15,
        help="Number of chunks to retrieve per query for annotation (default: 15).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file path (default: print to stdout).",
    )
    return parser.parse_args()


def build_search_engine(collection: str):
    """Build a fully wired HybridSearch for the given collection."""
    from src.core.settings import get_bm25_index_dir, load_settings
    from src.core.query_engine.query_processor import QueryProcessor
    from src.core.query_engine.hybrid_search import create_hybrid_search
    from src.core.query_engine.dense_retriever import create_dense_retriever
    from src.core.query_engine.sparse_retriever import create_sparse_retriever
    from src.ingestion.storage.bm25_indexer import BM25Indexer
    from src.libs.embedding.embedding_factory import EmbeddingFactory
    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

    settings = load_settings()

    vector_store = VectorStoreFactory.create(settings, collection_name=collection)
    embedding_client = EmbeddingFactory.create(settings)

    dense_retriever = create_dense_retriever(
        settings=settings,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    bm25_indexer = BM25Indexer(
        index_dir=str(get_bm25_index_dir(collection, settings))
    )
    sparse_retriever = create_sparse_retriever(
        settings=settings,
        bm25_indexer=bm25_indexer,
        vector_store=vector_store,
    )
    sparse_retriever.default_collection = collection

    query_processor = QueryProcessor()
    return create_hybrid_search(
        settings=settings,
        query_processor=query_processor,
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
    )


def main() -> int:
    args = parse_args()

    test_set_path = Path(args.test_set)
    if not test_set_path.exists():
        print(f"❌ Test set not found: {test_set_path}", file=sys.stderr)
        return 1

    with test_set_path.open("r", encoding="utf-8") as f:
        golden = json.load(f)

    test_cases = golden.get("test_cases", [])
    if not test_cases:
        print("❌ No test cases in golden set.", file=sys.stderr)
        return 1

    print(f"✅ Loaded {len(test_cases)} test cases from {test_set_path}")
    print(f"🔧 Building HybridSearch for collection: {args.collection}")

    search = build_search_engine(args.collection)
    print(f"✅ HybridSearch ready\n")

    output_cases: List[Dict[str, Any]] = []

    for idx, tc in enumerate(test_cases, 1):
        query = tc["query"]
        print(f"[{idx}/{len(test_cases)}] Searching: {query[:80]}...")

        try:
            results = search.search(query=query, top_k=args.top_k)
        except Exception as exc:
            print(f"    ⚠️  Search failed: {exc}")
            output_cases.append({
                "query": query,
                "reference_answer": tc.get("reference_answer", ""),
                "candidates": [],
            })
            continue

        candidates: List[Dict[str, Any]] = []
        for r in results:
            chunk_id = getattr(r, "chunk_id", "")
            score = getattr(r, "score", 0.0)
            text = getattr(r, "text", "")
            metadata = getattr(r, "metadata", {})

            candidates.append({
                "chunk_id": chunk_id,
                "score": round(score, 4),
                "section": metadata.get("section", ""),
                "chunk_type": metadata.get("chunk_type", ""),
                "source_path": metadata.get("source_path", ""),
                "text_preview": text[:300].replace("\n", " "),
            })

        print(f"    → {len(candidates)} candidates retrieved")

        output_cases.append({
            "query": query,
            "reference_answer": tc.get("reference_answer", ""),
            "candidates": candidates,
        })

    output = {
        "description": "Candidate chunks for golden test set annotation",
        "collection": args.collection,
        "top_k": args.top_k,
        "instructions": (
            "For each test case, review the candidates below. "
            "Copy chunk_id values that are genuinely relevant to the query "
            "into expected_chunk_ids in the golden test set. "
            "A chunk is relevant if it contains information that directly "
            "answers or supports answering the query."
        ),
        "test_cases": output_cases,
    }

    if args.output:
        out_path = Path(args.output)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n✅ Candidates written to {out_path}")
    else:
        print("\n" + json.dumps(output, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
