#!/usr/bin/env python
"""Evaluation script for Modular RAG MCP Server.

Runs batch evaluation against a golden test set and outputs a metrics report.

Usage:
    # Run with default settings (custom evaluator)
    python scripts/evaluate.py

    # Specify a custom golden test set
    python scripts/evaluate.py --test-set path/to/golden.json

    # Use a specific collection
    python scripts/evaluate.py --collection technical_docs

    # JSON output
    python scripts/evaluate.py --json

    # Validate a golden set without loading models or indexes
    python scripts/evaluate.py --validate-only

Exit codes:
    0 - Success
    1 - Evaluation failure
    2 - Configuration error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def configure_console_encoding() -> None:
    """Use UTF-8 for the real Windows CLI without replacing captured streams."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def parse_metric_threshold(value: str) -> tuple[str, float]:
    """Parse a ``metric=value`` CLI quality gate."""
    if "=" not in value:
        raise argparse.ArgumentTypeError("Expected METRIC=VALUE, for example recall_at_k=0.80")
    metric, raw_threshold = value.split("=", 1)
    metric = metric.strip()
    if not metric:
        raise argparse.ArgumentTypeError("Metric name cannot be empty")
    try:
        threshold = float(raw_threshold)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Metric threshold must be numeric") from exc
    if not 0.0 <= threshold <= 1.0:
        raise argparse.ArgumentTypeError("Metric threshold must be between 0 and 1")
    return metric, threshold


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run RAG evaluation against a golden test set."
    )
    parser.add_argument(
        "--test-set",
        default="tests/fixtures/golden_test_set.json",
        help="Path to golden test set JSON file (default: tests/fixtures/golden_test_set.json)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Collection name to search within.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of chunks to retrieve per query (default: 10).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of formatted text.",
    )
    parser.add_argument(
        "--validate-only",
        "--no-search",
        dest="validate_only",
        action="store_true",
        help="Validate the golden-set structure only; --no-search is a deprecated alias.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/evaluation_runs",
        help="Root directory for immutable run artifacts (default: data/evaluation_runs).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not persist evaluation artifacts.",
    )
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Return exit code 1 if any query has a non-success status.",
    )
    parser.add_argument(
        "--min-metric",
        action="append",
        default=[],
        type=parse_metric_threshold,
        metavar="METRIC=VALUE",
        help="Fail when an aggregate metric is below the threshold; may be repeated.",
    )
    parser.add_argument(
        "--allow-legacy-synthetic",
        action="store_true",
        help="Explicitly allow legacy list-form synthetic fixtures (not for quality claims).",
    )
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    if args.validate_only:
        try:
            from src.observability.evaluation.eval_runner import load_test_set

            cases = load_test_set(
                args.test_set,
                allow_legacy_synthetic=args.allow_legacy_synthetic,
            )
        except Exception as exc:
            print(f"❌ Golden-set validation failed: {exc}", file=sys.stderr)
            return 1
        print(f"✅ Golden set is valid: {args.test_set} ({len(cases)} cases)")
        return 0

    try:
        from src.core.settings import get_bm25_index_dir, load_settings
        from src.libs.evaluator.evaluator_factory import EvaluatorFactory
        from src.observability.evaluation.eval_runner import EvalRunner

        settings = load_settings()
    except Exception as exc:
        print(f"❌ Configuration error: {exc}", file=sys.stderr)
        return 2

    # Create evaluator from config
    try:
        evaluator = EvaluatorFactory.create(settings)
        evaluator_name = type(evaluator).__name__
    except Exception as exc:
        print(f"❌ Failed to create evaluator: {exc}", file=sys.stderr)
        return 2

    # Retrieval initialization is mandatory for a quality run. Infrastructure
    # errors must not be converted into an apparently valid zero-recall report.
    hybrid_search = None
    collection = args.collection or "default"
    try:
        from src.core.query_engine.query_processor import QueryProcessor
        from src.core.query_engine.hybrid_search import create_hybrid_search
        from src.core.query_engine.dense_retriever import create_dense_retriever
        from src.core.query_engine.sparse_retriever import create_sparse_retriever
        from src.ingestion.storage.bm25_indexer import BM25Indexer
        from src.libs.embedding.embedding_factory import EmbeddingFactory
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory

        vector_store = VectorStoreFactory.create(
            settings, collection_name=collection,
        )
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
        hybrid_search = create_hybrid_search(
            settings=settings,
            query_processor=query_processor,
            dense_retriever=dense_retriever,
            sparse_retriever=sparse_retriever,
        )
        print(
            f"✅ HybridSearch initialized for collection: {collection}",
            file=sys.stderr if args.json else sys.stdout,
        )
    except Exception as exc:
        print(f"❌ Failed to initialize search: {exc}", file=sys.stderr)
        return 2

    # Create and run EvalRunner
    runner = EvalRunner(
        settings=settings,
        hybrid_search=hybrid_search,
        evaluator=evaluator,
    )

    try:
        progress_stream = sys.stderr if args.json else sys.stdout
        print(f"\n🔍 Running evaluation with {evaluator_name}...", file=progress_stream)
        print(f"📄 Test set: {args.test_set}", file=progress_stream)
        print(f"🔢 Top-K: {args.top_k}\n", file=progress_stream)

        report = runner.run(
            test_set_path=args.test_set,
            top_k=args.top_k,
            collection=collection,
            allow_legacy_synthetic=args.allow_legacy_synthetic,
        )
    except Exception as exc:
        print(f"❌ Evaluation failed: {exc}", file=sys.stderr)
        return 1

    artifact_dir = None
    if not args.no_save:
        try:
            artifact_dir = report.save_artifacts(args.output_dir)
        except Exception as exc:
            print(f"❌ Failed to save evaluation artifacts: {exc}", file=sys.stderr)
            return 1

    # Output results
    if args.json:
        payload = report.to_dict()
        payload["artifact_dir"] = str(artifact_dir) if artifact_dir else None
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_report(report)
        if artifact_dir:
            print(f"Artifacts: {artifact_dir}")

    threshold_failures = []
    for metric, threshold in args.min_metric:
        actual = report.aggregate_metrics.get(metric)
        if actual is None:
            threshold_failures.append(f"{metric}: metric was not produced")
        elif actual < threshold:
            threshold_failures.append(
                f"{metric}: {actual:.4f} < required {threshold:.4f}"
            )
    if threshold_failures:
        for failure in threshold_failures:
            print(f"❌ Quality gate failed: {failure}", file=sys.stderr)

    has_errors = any(qr.status != "success" for qr in report.query_results)
    if threshold_failures or (args.fail_on_errors and has_errors):
        return 1
    return 0


def _print_report(report) -> None:
    """Print formatted evaluation report."""
    print("=" * 60)
    print("  EVALUATION REPORT")
    print("=" * 60)
    print(f"  Evaluator: {report.evaluator_name}")
    print(f"  Test Set:  {report.test_set_path}")
    print(f"  Queries:   {len(report.query_results)}")
    print(f"  Time:      {report.total_elapsed_ms:.0f} ms")
    print(f"  Run ID:    {report.run_id}")
    print(f"  Status:    {report.status_counts}")
    print()

    # Aggregate metrics
    print("─" * 60)
    print("  AGGREGATE METRICS")
    print("─" * 60)
    if report.aggregate_metrics:
        for metric, value in sorted(report.aggregate_metrics.items()):
            bar = "█" * int(value * 20) + "░" * (20 - int(value * 20))
            print(f"  {metric:<25s} {bar} {value:.4f}")
    else:
        print("  (no metrics computed)")
    print()

    # Per-query details
    print("─" * 60)
    print("  PER-QUERY RESULTS")
    print("─" * 60)
    for i, qr in enumerate(report.query_results, 1):
        print(f"\n  [{i}] {qr.query}")
        print(f"      Status: {qr.status}")
        print(f"      Retrieved: {len(qr.retrieved_chunk_ids)} chunks")
        if qr.error_message:
            print(f"      Error: {qr.error_type}: {qr.error_message}")
        if qr.metrics:
            for metric, value in sorted(qr.metrics.items()):
                print(f"      {metric}: {value:.4f}")
        else:
            print("      (no metrics)")
        print(f"      Time: {qr.elapsed_ms:.0f} ms")

    print()
    print("=" * 60)


if __name__ == "__main__":
    configure_console_encoding()
    sys.exit(main())
