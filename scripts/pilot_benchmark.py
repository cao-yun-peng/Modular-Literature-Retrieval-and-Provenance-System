"""20-question frozen-corpus pilot: validate, review, run and offline replay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.observability.evaluation.frozen_pilot import (  # noqa: E402
    FrozenCorpus,
    import_reviews,
    now,
    read,
    retrieve,
    score_run,
    sha,
    validate,
    write,
)
from src.observability.evaluation.frozen_pilot_review import export_review  # noqa: E402

ROOT = Path("data/paper_benchmark/papers48-pilot20-v1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=Path("data/baselines/papers48-20260916"))
    parser.add_argument("--dataset", type=Path, default=ROOT / "dataset.json")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate")
    check.add_argument("--allow-draft", action="store_true")
    check.add_argument("--output", type=Path, default=ROOT / "validation.json")
    review = sub.add_parser("review")
    review.add_argument("--output", type=Path, default=ROOT / "review.html")
    run = sub.add_parser("run")
    run.add_argument("--strategy", choices=["bm25", "dense", "hybrid"], default="bm25")
    run.add_argument("--query-vectors", type=Path)
    run.add_argument("--allow-draft", action="store_true")
    run.add_argument("--output", type=Path, required=True)
    replay = sub.add_parser("score")
    replay.add_argument("--run", type=Path, required=True)
    replay.add_argument("--allow-draft", action="store_true")
    replay.add_argument("--output", type=Path, required=True)
    approve = sub.add_parser("import-reviews")
    approve.add_argument("--reviews", type=Path, required=True)
    approve.add_argument("--output", type=Path, required=True)
    embed = sub.add_parser(
        "embed-queries", help="Send ONLY the 20 queries to configured DashScope; consumes API quota"
    )
    embed.add_argument("--config", default="config/settings.yaml")
    embed.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus, dataset = FrozenCorpus(args.baseline), read(args.dataset)
    # Reject protected/ambiguous destinations before any network activity.
    if args.output.resolve().is_relative_to(corpus.root):
        raise ValueError("Output must be outside frozen baseline")
    if args.output.resolve() == args.dataset.resolve():
        raise ValueError("Use a new output path; never overwrite the input dataset")
    allow = getattr(args, "allow_draft", True)
    validation = validate(dataset, corpus, allow_draft=allow)
    if args.command == "validate":
        write(args.output, validation, corpus.root)
        print(validation)
    elif args.command == "review":
        export_review(dataset, corpus, args.output)
        print(args.output.resolve())
    elif args.command == "import-reviews":
        updated = import_reviews(dataset, read(args.reviews))
        validate(updated, corpus, allow_draft=True)
        write(args.output, updated, corpus.root)
        print(args.output.resolve())
    elif args.command == "embed-queries":
        from src.core.settings import load_settings
        from src.libs.embedding.embedding_factory import EmbeddingFactory
        from src.observability.evaluation.benchmark_authoring import load_local_key

        if args.output.exists():
            raise FileExistsError("Query embedding cache exists; reuse it or choose a new path")
        load_local_key()
        settings = load_settings(args.config)
        frozen = corpus.checked("settings.json")["embedding"]
        identity = {
            k: getattr(settings.embedding, k)
            for k in ("provider", "model", "dimensions", "base_url")
        }
        if identity != {k: frozen[k] for k in identity}:
            raise ValueError("Configured query embedding differs from baseline")
        queries = [{"case_id": c["id"], "query": c["query"]} for c in dataset["cases"]]
        vectors = EmbeddingFactory.create(settings).embed([c["query"] for c in queries])
        if len(vectors) != len(queries):
            raise ValueError("Missing query embeddings")
        cache = {
            "baseline": corpus.identity(),
            "queries_sha256": sha(queries),
            "embedding": identity,
            "created_at": now(),
            "payload_scope": "20 queries only; no PDF, answers or evidence",
            "vectors": [
                {"case_id": q["case_id"], "embedding": v} for q, v in zip(queries, vectors)
            ],
        }
        write(args.output, cache, corpus.root)
        print(args.output.resolve())
    else:
        if args.command == "run":
            vectors = read(args.query_vectors) if args.query_vectors else None
            result = retrieve(dataset, corpus, strategy=args.strategy, query_vectors=vectors)
            write(args.output, result, corpus.root)
            report_path = args.output.with_name(args.output.stem + ".scores.json")
        else:
            result = read(args.run)
            report_path = args.output
        report = score_run(dataset, result, corpus, allow_draft=allow)
        write(report_path, report, corpus.root)
        print(
            {
                "report": str(report_path),
                "status": report["status"],
                "aggregate": report["aggregate"],
            }
        )


if __name__ == "__main__":
    main()
