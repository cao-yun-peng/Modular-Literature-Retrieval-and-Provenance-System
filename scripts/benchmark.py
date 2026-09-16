#!/usr/bin/env python
"""Paper benchmark lifecycle CLI. Run --help; original PDFs are never modified."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.observability.evaluation.benchmark_data import (
    digest,
    load_corpus,
    map_evidence,
    now,
    prepare_corpus,
    read_json,
    source_diagnostics,
    validate_dataset,
    write_json,
)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/paper_benchmark/v1"))
    p.add_argument("--config")
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser(
        "prepare", help="Inventory PDFs, canonical pages and split paper families"
    )
    prep.add_argument("--source", required=True, type=Path)
    prep.add_argument("--family-overrides", type=Path)
    sub.add_parser("ingest", help="Resume isolated full-pipeline ingestion with current embedding")
    author = sub.add_parser("generate", help="DeepSeek draft generation; never approves labels")
    author.add_argument("--pilot", action="store_true")
    author.add_argument("--workers", type=int, default=3)
    author.add_argument("--token-budget", type=int, default=2_000_000)
    author.add_argument("--output", type=Path)
    for name in ("validate", "freeze", "map", "review", "pool", "assemble", "diagnose"):
        sp = sub.add_parser(name)
        sp.add_argument("--dataset", required=True, type=Path)
        sp.add_argument("--output", type=Path)
        if name == "review":
            sp.add_argument("--blind", action="store_true")
        if name == "freeze":
            sp.add_argument("--version", required=True)
        if name == "pool":
            sp.add_argument("--split", choices=["dev", "test"], default="dev")
            sp.add_argument(
                "--from-run", type=Path, help="Reuse saved BM25/Dense/Hybrid Top-20 candidates"
            )
    judgments_review = sub.add_parser(
        "review-judgments", help="Review relevance and search omitted indexed evidence"
    )
    judgments_review.add_argument("--dataset", required=True, type=Path)
    judgments_review.add_argument("--judgments", required=True, type=Path)
    judgments_review.add_argument("--output", required=True, type=Path)
    run = sub.add_parser("run", help="Versioned retrieval or service benchmark")
    run.add_argument("--dataset", required=True, type=Path)
    run.add_argument("--strategies", nargs="+", default=["hybrid"])
    run.add_argument("--ks", nargs="+", type=int, default=[5, 10])
    run.add_argument("--split", choices=["dev", "test"], default="dev")
    run.add_argument("--judgments", type=Path)
    run.add_argument("--evidence-map", type=Path)
    run.add_argument("--selection", type=Path)
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--output", type=Path)
    replay = sub.add_parser("replay")
    replay.add_argument("--run", required=True, type=Path)
    replay.add_argument("--judgments", type=Path)
    replay.add_argument("--evidence-map", type=Path)
    replay.add_argument(
        "--dataset", type=Path, help="Updated labels; recorded query inputs must remain identical"
    )
    replay.add_argument(
        "--as-run", action="store_true", help="Write a new immutable run usable by compare/select"
    )
    replay.add_argument("--output", required=True, type=Path)
    comp = sub.add_parser("compare")
    comp.add_argument("--baseline", required=True, type=Path)
    comp.add_argument("--candidate", required=True, type=Path)
    comp.add_argument("--strategies", nargs=2, required=True)
    comp.add_argument("--output", required=True, type=Path)
    sel = sub.add_parser("select", help="Predeclare final strategies after a development run")
    sel.add_argument("--dev-run", type=Path, required=True)
    sel.add_argument("--dataset", type=Path, required=True)
    sel.add_argument("--strategies", nargs="+", required=True)
    sel.add_argument("--output", type=Path, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    root = args.root.resolve()
    if args.command == "prepare":
        result = prepare_corpus(
            args.source, root, read_json(args.family_overrides) if args.family_overrides else None
        )
        print(
            json.dumps(
                {
                    "corpus_id": result["corpus_id"],
                    "papers": len(result["papers"]),
                    "split_counts": result["split_counts"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    corpus, pages = load_corpus(root)
    if args.command == "generate":
        from src.core.settings import load_settings
        from src.observability.evaluation.benchmark_authoring import (
            export_review,
            generate,
            make_requests,
        )

        requests = make_requests(corpus, pages)
        write_json(root / "authoring_requests.json", requests)
        selected = [r for r in requests if r["pilot"]] if args.pilot else requests
        output = args.output or root / ("pilot" if args.pilot else "authoring")
        data = generate(
            selected,
            corpus,
            pages,
            output,
            load_settings(args.config),
            workers=args.workers,
            token_budget=args.token_budget,
        )
        if data["test_cases"]:
            validate_dataset(data, corpus, pages)
            export_review(data, corpus, root, output / "review.html")
        return 0 if len(data["test_cases"]) == len(selected) else 1
    if args.command in (
        "validate",
        "freeze",
        "review",
        "map",
        "pool",
        "run",
        "select",
        "assemble",
        "review-judgments",
        "diagnose",
    ):
        data = read_json(args.dataset)
        validate_dataset(data, corpus, pages)
    if args.command == "diagnose":
        target = args.output or root / "source_diagnostics.json"
        write_json(target, source_diagnostics(corpus, pages, data))
        print(target)
        return 0
    if args.command == "validate":
        if data.get("status") == "frozen":
            validate_dataset(data, corpus, pages, freeze=True)
        print(f"Valid: {len(data['test_cases'])} cases; status={data.get('status', 'draft')}")
        return 0
    if args.command == "assemble":
        selected = {}
        for case in data["test_cases"]:
            if case.get("review", {}).get("status") == "rejected":
                continue
            slot = case.get("slot_id", case["id"])
            if slot not in selected or case.get("review", {}).get("status") == "approved":
                selected[slot] = case
        data["test_cases"] = [selected[s] for s in sorted(selected)]
        for case in data["test_cases"]:
            if case["query_type"] == "locator":
                case["scope_paper_ids"] = case["source_paper_ids"]
                case["query_params"] = {"retrieval_mode": "section", "expand_context": "parent"}
        data.update(status="draft", version="draft-120")
        validate_dataset(data, corpus, pages)
        write_json(args.output or root / "draft_120.json", data)
        print(f"Assembled {len(selected)} draft slots; human approval required")
        return 0 if len(selected) == 120 else 1
    if args.command == "freeze":
        data["test_cases"] = [
            c for c in data["test_cases"] if c.get("review", {}).get("status") == "approved"
        ]
        validate_dataset(data, corpus, pages, freeze=True)
        data.update(status="frozen", version=args.version, frozen_at=now())
        target = args.output or root / f"golden-{args.version}.json"
        if target.exists():
            raise ValueError("Frozen versions are immutable")
        write_json(target, data)
        print(f"Frozen {target}; SHA256={digest(data)}")
        return 0
    if args.command == "review":
        from src.observability.evaluation.benchmark_authoring import export_review

        target = args.output or root / "review.html"
        export_review(data, corpus, root, target, blind=args.blind)
        print(target)
        return 0
    if args.command == "compare":
        from src.observability.evaluation.benchmark_runner import (
            compare_runs,
            write_comparison_report,
        )

        result = compare_runs(args.baseline, args.candidate, *args.strategies)
        write_json(args.output, result)
        write_comparison_report(args.output.with_suffix(".md"), result)
        print(
            json.dumps({"passed": result["passed"], "output": str(args.output)}, ensure_ascii=False)
        )
        return 0 if result["passed"] else 1
    from src.observability.evaluation.benchmark_runtime import isolated_settings

    settings = isolated_settings(root, args.config)
    collection = "paper-benchmark-" + root.name
    if args.command == "ingest":
        from src.observability.evaluation.benchmark_runtime import ingest_corpus

        index = ingest_corpus(root, corpus, settings, collection)
        print(f"Indexed {len(index['chunks'])} chunks")
        return 0 if all(d.get("success") for d in index["ingestion"].values()) else 1
    index = read_json(root / "index.json")
    if args.command == "pool" and args.from_run:
        from src.observability.evaluation.benchmark_runner import pool_saved_candidates

        if args.split == "test":
            validate_dataset(data, corpus, pages, freeze=True)
        write_json(
            args.output or root / f"judgments-{args.split}.json",
            pool_saved_candidates(args.from_run, data, index, args.split),
        )
        return 0
    if args.command == "review-judgments":
        from src.observability.evaluation.benchmark_review import export_judgments
        from src.observability.evaluation.benchmark_runner import validate_judgments

        judgments = read_json(args.judgments)
        validate_judgments(judgments, index, data)
        export_judgments(data, corpus, index, judgments, root, args.output)
        print(args.output)
        return 0
    if args.command == "map":
        write_json(args.output or root / "evidence_map.json", map_evidence(data, index))
        return 0
    if args.command == "replay":
        from src.observability.evaluation.benchmark_runner import replay

        summary = replay(
            args.run,
            corpus,
            pages,
            index,
            read_json(args.judgments) if args.judgments else None,
            read_json(args.evidence_map) if args.evidence_map else None,
            data=read_json(args.dataset) if args.dataset else None,
            output_run=args.output if args.as_run else None,
        )
        if not args.as_run:
            write_json(args.output, summary)
        print(args.output)
        return 0
    if args.command == "select":
        previous = read_json(args.dev_run / "manifest.json")
        if previous["split"] != "dev" or previous["status"] != "complete":
            raise ValueError("Selection requires a complete, reviewed development run")
        if previous["dataset_id"] != digest(data) or previous["index_id"] != index["index_id"]:
            raise ValueError("Development run uses another data/index version")
        if not set(args.strategies) <= set(previous["strategies"]):
            raise ValueError("Final strategies must have been evaluated on development data")
        if args.output.exists():
            raise ValueError("Selection file already exists")
        write_json(
            args.output,
            {
                "declared_at": now(),
                "dev_run": str(args.dev_run),
                "dataset_id": digest(data),
                "index_id": index["index_id"],
                "strategies": args.strategies,
            },
        )
        return 0
    from src.observability.evaluation.benchmark_runtime import BenchmarkAdapter, snapshot_index

    live_index = snapshot_index(settings, corpus, index["collection"])
    if live_index["index_id"] != index["index_id"] or live_index.get("storage_id") != index.get(
        "storage_id"
    ):
        raise ValueError("Live index has changed since its snapshot")
    adapter = BenchmarkAdapter(settings, index)
    if args.command == "pool":
        from src.observability.evaluation.benchmark_runner import pool_candidates

        if args.split == "test":
            validate_dataset(data, corpus, pages, freeze=True)
        write_json(
            args.output or root / f"judgments-{args.split}.json",
            pool_candidates(data, adapter, index, args.split),
        )
        return 0
    from src.observability.evaluation.benchmark_runner import run_benchmark

    index["environment"] = live_index["environment"]
    result = run_benchmark(
        data,
        corpus,
        pages,
        index,
        adapter,
        args.output or root / "runs",
        strategies=args.strategies,
        ks=args.ks,
        split=args.split,
        repeats=args.repeats,
        judgments=read_json(args.judgments) if args.judgments else None,
        evidence_map=read_json(args.evidence_map) if args.evidence_map else None,
        final_selection=read_json(args.selection) if args.selection else None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Benchmark error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
