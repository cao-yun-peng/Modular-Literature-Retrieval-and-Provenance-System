#!/usr/bin/env python
"""Run a same-candidate RRF versus Cross-Encoder reranking ablation."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare RRF ordering with Cross-Encoder ordering over identical candidates."
    )
    parser.add_argument(
        "--test-set",
        default="tests/fixtures/golden_test_set.json",
        help="Versioned golden test set.",
    )
    parser.add_argument("--collection", default="eval_test", help="Collection to evaluate.")
    parser.add_argument("--candidate-k", type=int, default=20, help="RRF candidate pool size.")
    parser.add_argument("--top-k", type=int, default=10, help="Output depth for both arms.")
    parser.add_argument(
        "--model",
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        help="sentence-transformers Cross-Encoder model name or local path.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/evaluation_ablation_runs",
        help="Root for immutable run artifacts.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Load an already-cached model without contacting Hugging Face Hub.",
    )
    parser.add_argument("--no-save", action="store_true", help="Do not save run artifacts.")
    return parser.parse_args(argv)


def _package_versions() -> dict[str, str]:
    versions = {}
    for package in ("sentence-transformers", "torch", "transformers"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _create_hybrid_search(settings, collection: str):
    from src.core.query_engine.dense_retriever import create_dense_retriever
    from src.core.query_engine.hybrid_search import create_hybrid_search
    from src.core.query_engine.query_processor import QueryProcessor
    from src.core.query_engine.sparse_retriever import create_sparse_retriever
    from src.core.settings import get_bm25_index_dir
    from src.ingestion.storage.bm25_indexer import BM25Indexer
    from src.libs.embedding.embedding_factory import EmbeddingFactory
    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

    vector_store = VectorStoreFactory.create(settings, collection_name=collection)
    embedding_client = EmbeddingFactory.create(settings)
    dense_retriever = create_dense_retriever(settings, embedding_client, vector_store)
    bm25_indexer = BM25Indexer(index_dir=str(get_bm25_index_dir(collection, settings)))
    sparse_retriever = create_sparse_retriever(settings, bm25_indexer, vector_store)
    sparse_retriever.default_collection = collection
    return create_hybrid_search(
        settings=settings,
        query_processor=QueryProcessor(),
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.offline:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from src.core.settings import load_settings
        from src.libs.reranker.cross_encoder_reranker import CrossEncoderReranker
        from src.observability.evaluation.eval_runner import load_test_set
        from src.observability.evaluation.retrieval_ablation import (
            AblationConfig,
            run_ablation,
            save_ablation_artifacts,
        )

        config = AblationConfig(
            collection=args.collection,
            candidate_k=args.candidate_k,
            top_k=args.top_k,
            model=args.model,
        )
        config.validate()
        cases = load_test_set(args.test_set)
        settings = load_settings()
        rerank_settings = replace(
            settings.rerank,
            enabled=True,
            provider="cross_encoder",
            model=args.model,
            top_k=args.top_k,
        )
        experiment_settings = replace(settings, rerank=rerank_settings)

        print(f"Initializing Hybrid Search collection: {args.collection}", file=sys.stderr)
        hybrid_search = _create_hybrid_search(experiment_settings, args.collection)
        print(f"Loading Cross-Encoder: {args.model}", file=sys.stderr)
        reranker = CrossEncoderReranker(experiment_settings)
        print(
            f"Running {len(cases)} queries (candidate_k={args.candidate_k}, top_k={args.top_k})...",
            file=sys.stderr,
        )
        summary, per_query = run_ablation(
            cases=cases,
            hybrid_search=hybrid_search,
            reranker=reranker,
            config=config,
        )
        artifact_dir = None
        if not args.no_save:
            artifact_dir = save_ablation_artifacts(
                output_root=args.output_dir,
                config=config,
                test_set_path=args.test_set,
                summary=summary,
                per_query=per_query,
                package_versions=_package_versions(),
            )
        output = {"summary": summary, "artifact_dir": str(artifact_dir) if artifact_dir else None}
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Ablation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
