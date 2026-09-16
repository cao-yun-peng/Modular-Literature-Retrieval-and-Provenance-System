"""Immutable runs, offline replay, graded pooling and paired regression gates."""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from pathlib import Path

from .benchmark_data import (
    SCHEMA,
    digest,
    index_identity,
    now,
    read_json,
    validate_dataset,
    write_json,
)
from .benchmark_mapping import mapped_cases
from .benchmark_scoring import paired_bootstrap, score_case, summarize


def validate_index(index: dict, data: dict) -> None:
    if index["corpus_id"] != data["corpus_id"] or index["index_id"] != index_identity(
        index["chunks"]
    ):
        raise ValueError("Index identity mismatch; snapshot the intended index")


def validate_judgments(judgments: dict, index: dict, data: dict) -> None:
    if not judgments:
        return
    if judgments.get("index_id") != index["index_id"] or judgments.get("dataset_id") != digest(
        data
    ):
        raise ValueError("Judgments are bound to another index/dataset version")
    ids = {c["chunk_id"] for c in index["chunks"]}
    for cid, rows in judgments.get("cases", {}).items():
        if cid not in {c["id"] for c in data["test_cases"]}:
            raise ValueError("Judgment refers to unknown case")
        for chunk_id, row in rows.items():
            if (
                chunk_id not in ids
                or row.get("grade") not in (None, 0, 1, 2)
                or isinstance(row.get("grade"), bool)
            ):
                raise ValueError("Invalid judgment chunk/grade")


def record_error_scores(row: dict, case: dict) -> None:
    """Runtime failures remain in applicable metric denominators, even without a response."""
    if row["status"] != "error":
        return
    expected = (
        {"source_traceability", "page_completeness", "page_accuracy", "citation_correctness"}
        if case["answerable"]
        else set()
    )
    if case["answerable"]:
        expected.update(("mrr_at_k", "direct_mrr_at_k", "ndcg_at_k"))
    for key, value in row["metrics"].items():
        if (value is not None or key in expected) and key != "evidence_retention":
            row["metrics"][key] = 0.0
            row["metric_states"][key] = "error"


def run_benchmark(
    data: dict,
    corpus: dict,
    pages: dict,
    index: dict,
    adapter,
    output: Path,
    *,
    strategies=("hybrid",),
    ks=(5, 10),
    split="dev",
    judgments: dict | None = None,
    repeats=1,
    budget=12000,
    final_selection: dict | None = None,
    evidence_map: dict | None = None,
) -> dict:
    validate_dataset(data, corpus, pages, freeze=data.get("status") == "frozen")
    validate_index(index, data)
    judgments = judgments or {}
    validate_judgments(judgments, index, data)
    if any(k not in range(1, 21) for k in ks) or repeats < 1:
        raise ValueError("K must be 1..20 and repeats positive")
    allowed = {
        "bm25",
        "dense",
        "hybrid",
        "service",
        "rrf_pool",
        "cross_encoder",
        "llm",
        "expand_none",
        "expand_neighbors",
        "expand_parent",
    }
    if not set(strategies) <= allowed:
        raise ValueError("Unknown strategy")
    cases = [c for c in mapped_cases(data, index, evidence_map) if c["split"] == split]
    if not cases:
        raise ValueError("No cases in selected split")
    if split == "test":
        validate_dataset(data, corpus, pages, freeze=True)
        if data.get("status") != "frozen" or not final_selection:
            raise ValueError("Test split requires frozen labels and predeclared final selection")
        if (
            final_selection.get("dataset_id") != digest(data)
            or set(strategies) != set(final_selection.get("strategies", []))
            or final_selection.get("index_id") != index["index_id"]
        ):
            raise ValueError("Final selection does not match this experiment")
    run_dir = output / now().replace(":", "").replace("+", "_")
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": SCHEMA,
        "started_at": now(),
        "dataset_id": digest(data),
        "corpus_id": corpus["corpus_id"],
        "index_id": index["index_id"],
        "storage_id": index.get("storage_id"),
        "split": split,
        "strategies": list(strategies),
        "ks": list(ks),
        "repeats": repeats,
        "budget": budget,
        "status": "running",
        "final_selection": final_selection,
        "environment": index.get("environment"),
        "judgments_id": digest(judgments),
        "mapping_id": digest(evidence_map or {}),
        "cache_protocol": "First request per strategy and subsequent warm requests; host/model cold start is not controlled.",
        "corpus_parse_success_rate": sum(p["status"] == "parsed" for p in corpus["papers"])
        / len(corpus["papers"]),
        "ingestion_success_rate": (
            sum(bool(d.get("success")) for d in index.get("ingestion", {}).values())
            / len(corpus["papers"])
        )
        if index.get("ingestion")
        else None,
    }
    write_json(run_dir / "manifest.json", manifest)
    # Store label/config inputs for replay. PDF/source-page files are addressed by
    # their hashes in the corpus snapshot, not copied into every run.
    write_json(run_dir / "dataset.json", data)
    write_json(run_dir / "judgments.json", judgments)
    write_json(run_dir / "mapping.json", evidence_map or {})
    papers = {p["paper_id"]: p for p in corpus["papers"]}
    rows, warmed = [], set()
    shared = {}
    with (run_dir / "query_results.jsonl").open("w", encoding="utf-8") as stream:
        for repeat in range(1, repeats + 1):
            for case in cases:
                pids = case["source_paper_ids"]
                for strategy in strategies:
                    ranked = None
                    rank_elapsed = None
                    for k in ks:
                        reused = ranked is not None and not strategy.startswith("expand_")
                        row = {
                            "case_id": case["id"],
                            "query": case["query"],
                            "query_type": case["query_type"],
                            "language": case["language"],
                            "split": split,
                            "strategy": strategy,
                            "k": k,
                            "repeat": repeat,
                            "family_ids": sorted({papers[p]["family_id"] for p in pids}),
                            "topics": sorted({t for p in pids for t in papers[p]["topics"]}),
                            "request": {
                                **case.get("query_params", {}),
                                "query": case["query"],
                                "top_k": k,
                                "collection": index["collection"],
                            },
                            "cache_state": "warm" if strategy in warmed else "first_request",
                            "status": "success",
                        }
                        started = time.perf_counter()
                        try:
                            if strategy in (
                                "rrf_pool",
                                "cross_encoder",
                                "llm",
                            ) or strategy.startswith("expand_"):
                                if case["id"] not in shared:
                                    shared[case["id"]] = adapter.query(case, 20, "hybrid")
                                if ranked is None or strategy.startswith("expand_"):
                                    transform_start = time.perf_counter()
                                    ranked = adapter.from_pool(
                                        case, shared[case["id"]], strategy, top_k=k
                                    )
                                    rank_elapsed = (
                                        time.perf_counter() - transform_start
                                    ) * 1000 + (shared[case["id"]].get("retrieval_ms") or 0)
                                response = {**ranked, "evidence": ranked["evidence"][:k]}
                                row["timing_scope"] = (
                                    "shared-pool ablation; same ranking scored at each K"
                                )
                            else:
                                response = adapter.query(case, k, strategy)
                                row["timing_scope"] = "independent real query"
                            row["fallback"] = bool(response.get("fallback"))
                            if row["fallback"]:
                                row["status"] = "degraded"
                            if response.get("observation_errors"):
                                row.update(
                                    status="error", error="; ".join(response["observation_errors"])
                                )
                        except Exception as exc:
                            row.update(
                                status="error", error_type=type(exc).__name__, error=str(exc)
                            )
                            response = {"evidence": [], "coverage": {"signal": "not_evaluated"}}
                        row["elapsed_ms"] = (
                            rank_elapsed
                            if rank_elapsed is not None
                            else (time.perf_counter() - started) * 1000
                        )
                        warmed.add(strategy)
                        row["response"] = response
                        t = time.perf_counter()
                        row.update(
                            score_case(
                                case,
                                response,
                                pages,
                                judgments.get("cases", {}).get(case["id"], {}),
                                index["chunks"],
                                k,
                                budget,
                            )
                        )
                        record_error_scores(row, case)
                        row["scoring_ms"] = (time.perf_counter() - t) * 1000
                        row["usage"] = collect_usage(response.get("trace"))
                        row["usage"]["reused_response"] = reused
                        if reused:
                            row["usage"].update(
                                recorded_calls=0,
                                tokens={key: 0 for key in row["usage"]["tokens"]},
                                availability="reused; no new call",
                            )
                        rows.append(row)
                        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                        stream.flush()
                        print(
                            f"EVAL {case['id']} {strategy} K={k} repeat={repeat}: {row['status']}",
                            flush=True,
                        )
    approved = all(c.get("review", {}).get("status") == "approved" for c in cases)
    unjudged = any(
        r["metrics"]["judged_at_k"] < 1 or "unjudged" in r["metric_states"].values() for r in rows
    )
    errors = any(r["status"] != "success" for r in rows)
    llm_service = (
        "service" in strategies
        and index.get("environment", {}).get("settings", {}).get("rerank", {}).get("enabled")
        and index.get("environment", {}).get("settings", {}).get("rerank", {}).get("provider")
        == "llm"
    )
    repeat_ok = ("llm" not in strategies and not llm_service) or repeats >= 3
    standard_ks = {5, 10} <= set(ks)
    manifest.update(
        status="failed"
        if errors
        else "provisional"
        if (
            not approved
            or unjudged
            or data.get("status") != "frozen"
            or not repeat_ok
            or not standard_ks
        )
        else "complete",
        finished_at=now(),
        human_review_complete=approved,
        unjudged=unjudged,
        llm_repeats_sufficient=repeat_ok,
        standard_ks_present=standard_ks,
    )
    write_json(run_dir / "manifest.json", manifest)
    summary = summarize(rows)
    write_json(run_dir / "summary.json", summary)
    write_report(run_dir, manifest, summary)
    return {"run_dir": str(run_dir), "status": manifest["status"], "rows": len(rows)}


def collect_usage(trace: dict | None) -> dict:
    stages = trace.get("stages", []) if trace else []
    calls = [s["data"] for s in stages if s["stage"] == "llm_usage"]
    return {
        "recorded_calls": len(calls),
        "tokens": {
            key: sum((c.get("usage") or {}).get(key, 0) for c in calls)
            if calls and all(c.get("usage") for c in calls)
            else None
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "availability": "recorded" if calls else "not_reported",
    }


def write_report(root, manifest, summary):
    lines = [
        "# Paper evidence benchmark",
        "",
        f"Status: **{manifest['status']}**",
        "",
        "Draft/unjudged/degraded runs must not be presented as validated project quality.",
        "",
        "| Slice | N | Evidence recall | All evidence | MRR | nDCG | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    def fmt(v):
        return "N/A" if v is None else f"{v:.4f}"

    for key, group in summary.items():
        m = group["metrics"]
        vals = [
            fmt(m.get(n, {}).get("value"))
            for n in ("evidence_recall_at_k", "all_evidence_at_k", "mrr_at_k", "ndcg_at_k")
        ]
        lines.append(f"| {key} | {group['count']} | {' | '.join(vals)} | {fmt(group['p95_ms'])} |")
    lines += [
        "",
        "Metric denominators and missing/error/N/A states are in summary.json.",
        "Per-query source evidence, candidates and failure details are in query_results.jsonl.",
        "Full-text handoff through real Zotero attachments: not covered by the manual PDF corpus.",
    ]
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def iter_rows(run_dir: Path):
    with (run_dir / "query_results.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def read_rows(run_dir: Path) -> list[dict]:
    return list(iter_rows(run_dir))


def replay(
    run_dir: Path,
    corpus: dict,
    pages: dict,
    index: dict,
    judgments: dict | None = None,
    evidence_map: dict | None = None,
    data: dict | None = None,
    output_run: Path | None = None,
) -> dict:
    original_data = read_json(run_dir / "dataset.json")
    data = data if data is not None else original_data
    manifest = read_json(run_dir / "manifest.json")
    validate_dataset(data, corpus, pages, freeze=data.get("status") == "frozen")
    validate_index(index, data)
    if index["index_id"] != manifest["index_id"]:
        raise ValueError("Replay index differs from recorded run")
    new_cases = {c["id"]: c for c in data["test_cases"]}
    inputs = ("query", "query_params", "scope_paper_ids", "split")
    for prior in original_data["test_cases"]:
        if prior["split"] != manifest["split"]:
            continue
        updated = new_cases.get(prior["id"])
        if updated is None or any(prior.get(key) != updated.get(key) for key in inputs):
            raise ValueError(
                "Query inputs changed; execute retrieval again instead of relabeling saved responses"
            )
    judgments = judgments if judgments is not None else read_json(run_dir / "judgments.json")
    validate_judgments(judgments, index, data)
    if evidence_map is None:
        evidence_map = (
            read_json(run_dir / "mapping.json") if (run_dir / "mapping.json").exists() else {}
        )
    cases = {c["id"]: c for c in mapped_cases(data, index, evidence_map)}
    if output_run:
        output_run.mkdir(parents=True, exist_ok=False)
    rows = []
    papers = {p["paper_id"]: p for p in corpus["papers"]}
    with (
        (output_run / "query_results.jsonl").open("w", encoding="utf-8")
        if output_run
        else nullcontext(None)
    ) as stream:
        for row in iter_rows(run_dir):
            case = cases[row["case_id"]]
            started = time.perf_counter()
            row.update(
                score_case(
                    case,
                    row["response"],
                    pages,
                    judgments.get("cases", {}).get(row["case_id"], {}),
                    index["chunks"],
                    row["k"],
                    row["characters"]["budget"],
                )
            )
            record_error_scores(row, case)
            row.update(
                query_type=case["query_type"],
                language=case["language"],
                family_ids=sorted({papers[p]["family_id"] for p in case["source_paper_ids"]}),
                topics=sorted({t for p in case["source_paper_ids"] for t in papers[p]["topics"]}),
            )
            if stream:
                row["source_scoring_ms"] = row.get("source_scoring_ms", row["scoring_ms"])
                row["scoring_ms"] = (time.perf_counter() - started) * 1000
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            # Summaries only need trace timings, not repeated candidate content.
            row["response"] = {
                "trace": {
                    "stages": [
                        {k: s[k] for k in ("stage", "elapsed_ms") if k in s}
                        for s in (row.get("response", {}).get("trace") or {}).get("stages", [])
                    ]
                }
            }
            rows.append(row)
    summary = summarize(rows)
    if output_run:
        expected = {
            (c["id"], s, k, r)
            for c in data["test_cases"]
            if c["split"] == manifest["split"]
            for s in manifest["strategies"]
            for k in manifest["ks"]
            for r in range(1, manifest["repeats"] + 1)
        }
        observed = {(r["case_id"], r["strategy"], r["k"], r["repeat"]) for r in rows}
        full_grid = observed == expected and len(rows) == len(expected)
        unjudged = any(
            r["metrics"]["judged_at_k"] < 1 or "unjudged" in r["metric_states"].values()
            for r in rows
        )
        errors = any(r["status"] != "success" for r in rows)
        formal = (
            data.get("status") == "frozen"
            and full_grid
            and not unjudged
            and manifest.get("llm_repeats_sufficient", False)
            and {5, 10} <= set(manifest["ks"])
        )
        manifest.update(
            source_run=str(run_dir.resolve()),
            execution_finished_at=manifest.get(
                "execution_finished_at", manifest.get("finished_at")
            ),
            finished_at=now(),
            replayed_at=now(),
            additional_model_calls=0,
            dataset_id=digest(data),
            judgments_id=digest(judgments),
            mapping_id=digest(evidence_map or {}),
            status="failed" if errors else "complete" if formal else "provisional",
            unjudged=unjudged,
            case_coverage_complete=full_grid,
            human_review_complete=all(
                c.get("review", {}).get("status") == "approved" for c in cases.values()
            ),
            scoring_source_sha256=digest(
                {
                    p.name: digest(p.read_bytes())
                    for p in Path(__file__).parent.glob("benchmark_*.py")
                }
            ),
        )
        for name, value in (
            ("manifest", manifest),
            ("dataset", data),
            ("judgments", judgments),
            ("mapping", evidence_map or {}),
            ("summary", summary),
        ):
            write_json(output_run / f"{name}.json", value)
        write_report(output_run, manifest, summary)
    return summary


def pool_candidates(data: dict, adapter, index: dict, split="dev") -> dict:
    result = {
        "schema_version": SCHEMA,
        "dataset_id": digest(data),
        "index_id": index["index_id"],
        "version": "pending-1",
        "purpose": "human annotation only; contains no test scores",
        "cases": {},
    }
    for case in data["test_cases"]:
        if case["split"] != split:
            continue
        pooled = {}
        for strategy in ("bm25", "dense", "hybrid"):
            response = adapter.query(case, 20, strategy)
            if response.get("fallback"):
                raise RuntimeError("Cannot pool a degraded retrieval")
            for item in response["evidence"]:
                entry = pooled.setdefault(
                    item["chunk_id"],
                    {
                        "grade": None,
                        "reviewer": None,
                        "reviewed_at": None,
                        "text": item["text"],
                        "paper_id": item["paper_id"],
                        "retrievers": [],
                    },
                )
                entry["retrievers"].append(strategy)
        result["cases"][case["id"]] = pooled
        print(f"POOL {case['id']}: {len(pooled)} candidates", flush=True)
    return result


def pool_saved_candidates(run_dir: Path, data: dict, index: dict, split="dev") -> dict:
    manifest = read_json(run_dir / "manifest.json")
    if manifest["dataset_id"] != digest(data) or manifest["index_id"] != index["index_id"]:
        raise ValueError("Saved pool uses another dataset/index")
    required = {"bm25", "dense", "hybrid"}
    result = {
        "schema_version": SCHEMA,
        "dataset_id": digest(data),
        "index_id": index["index_id"],
        "version": "pending-1",
        "source_run": str(run_dir),
        "cases": {},
    }
    observed = {}
    expected = {c["id"] for c in data["test_cases"] if c["split"] == split}
    for row in iter_rows(run_dir):
        cid, strategy = row["case_id"], row["strategy"]
        if cid not in expected or strategy not in required:
            continue
        if row["status"] != "success":
            raise ValueError("Failed/degraded responses cannot populate a formal candidate pool")
        observed.setdefault(cid, set()).add(strategy)
        for item in row["response"]["candidates"][:20]:
            entry = (
                result["cases"]
                .setdefault(cid, {})
                .setdefault(
                    item["chunk_id"],
                    {
                        "grade": None,
                        "reviewer": None,
                        "reviewed_at": None,
                        "text": item["text"],
                        "paper_id": item["paper_id"],
                        "retrievers": [],
                    },
                )
            )
            if strategy not in entry["retrievers"]:
                entry["retrievers"].append(strategy)
        result["cases"].setdefault(cid, {})
    if set(observed) != expected or any(found != required for found in observed.values()):
        raise ValueError("Saved run must contain all three retrievers for every selected case")
    return result


def compare_runs(a: Path, b: Path, strategy_a: str, strategy_b: str, k=None) -> dict:
    config = read_json(Path(__file__).resolve().parents[3] / "config/benchmark_regression.json")
    k = config["k"] if k is None else k
    ma, mb = read_json(a / "manifest.json"), read_json(b / "manifest.json")
    ma.setdefault("mapping_id", digest({}))
    mb.setdefault("mapping_id", digest({}))
    for field in (
        "dataset_id",
        "corpus_id",
        "index_id",
        "storage_id",
        "split",
        "budget",
        "judgments_id",
        "mapping_id",
    ):
        if ma.get(field) != mb.get(field):
            raise ValueError(f"Incompatible comparison: {field}")
    left = [r for r in read_rows(a) if r["strategy"] == strategy_a and r["k"] == k]
    right = [r for r in read_rows(b) if r["strategy"] == strategy_b and r["k"] == k]
    if not left or not right:
        raise ValueError("Selected strategy/K missing")
    metrics = {}
    for m in (
        "evidence_recall_at_k",
        "all_evidence_at_k",
        "expanded_evidence_recall",
        "budgeted_evidence_recall",
        "document_hit_at_k",
        "citation_correctness",
        "mrr_at_k",
        "ndcg_at_k",
    ):
        eligible_a = {r["case_id"] for r in left if r["metrics"].get(m) is not None}
        eligible_b = {r["case_id"] for r in right if r["metrics"].get(m) is not None}
        metrics[m] = (
            paired_bootstrap(
                left, right, m, samples=config["bootstrap_samples"], seed=config["bootstrap_seed"]
            )
            if eligible_a == eligible_b
            else {
                "delta": None,
                "ci95": None,
                "effective_groups": 0,
                "status": "not_comparable",
                "reason": "Different applicable metric denominators; no cases were silently removed",
                "baseline_only_cases": sorted(eligible_a - eligible_b),
                "candidate_only_cases": sorted(eligible_b - eligible_a),
            }
        )
    from .benchmark_scoring import aggregate

    p1, p2 = aggregate(left)["p95_ms"], aggregate(right)["p95_ms"]
    gates = {
        "evidence_recall": metrics["evidence_recall_at_k"]["delta"] is not None
        and metrics["evidence_recall_at_k"]["delta"]
        >= config["minimum_deltas"]["evidence_recall_at_k"],
        "document_hit": metrics["document_hit_at_k"]["delta"] is not None
        and metrics["document_hit_at_k"]["delta"] >= config["minimum_deltas"]["document_hit_at_k"],
        "citation_correctness": metrics["citation_correctness"]["delta"] is not None
        and metrics["citation_correctness"]["delta"]
        >= config["minimum_deltas"]["citation_correctness"],
        "p95": p1 is not None and p2 is not None and p2 <= p1 * config["maximum_p95_ratio"],
        "formal_runs": ma["status"] == mb["status"] == "complete",
    }
    by_case = {}
    for row in left:
        by_case.setdefault(row["case_id"], {"query": row["query"], "left": [], "right": []})[
            "left"
        ].append(row)
    for row in right:
        by_case.setdefault(row["case_id"], {"query": row["query"], "left": [], "right": []})[
            "right"
        ].append(row)
    cases = []
    for cid, record in by_case.items():
        changes = {}
        for metric in metrics:
            values = [
                [
                    r["metrics"].get(metric)
                    for r in record[side]
                    if r["metrics"].get(metric) is not None
                ]
                for side in ("left", "right")
            ]
            means = [sum(v) / len(v) if v else None for v in values]
            changes[metric] = {
                "baseline": means[0],
                "candidate": means[1],
                "delta": means[1] - means[0] if None not in means else None,
            }
        cases.append({"case_id": cid, "query": record["query"], "metrics": changes})
    return {
        "metrics": metrics,
        "strategies": [strategy_a, strategy_b],
        "k": k,
        "per_case": cases,
        "baseline_status": ma["status"],
        "candidate_status": mb["status"],
        "gate_config": config,
        "p95_ms": [p1, p2],
        "gates": gates,
        "passed": all(gates.values()),
        "note": "Engineering gates are not statistical significance claims",
    }


def write_comparison_report(target: Path, result: dict) -> None:
    lines = [
        "# 论文证据测评对照",
        "",
        f"方案：{' → '.join(result['strategies'])}；K={result['k']}。",
        "",
        f"运行状态：{result['baseline_status']} / {result['candidate_status']}。回归门槛通过：{result['passed']}。",
        "",
        "临时结果只用于诊断；未人工审核的草稿不能作为项目正式质量结论。",
        "",
        "| 指标 | 同题平均差值 | 95% CI | 有效论文组数 |",
        "|---|---:|---|---:|",
    ]
    for name, value in result["metrics"].items():
        delta = "N/A" if value["delta"] is None else f"{value['delta']:+.4f}"
        ci = "N/A" if value["ci95"] is None else " / ".join(f"{v:+.4f}" for v in value["ci95"])
        lines.append(f"| {name} | {delta} | {ci} | {value['effective_groups']} |")
    lines += ["", f"P95 延迟（ms）：{result['p95_ms']}。", "", "| 工程门槛 | 通过 |", "|---|---|"]
    lines += [f"| {name} | {passed} |" for name, passed in result["gates"].items()]
    example_metric = (
        "budgeted_evidence_recall"
        if any(s.startswith("expand_") for s in result["strategies"])
        else "evidence_recall_at_k"
    )
    eligible = [c for c in result["per_case"] if c["metrics"][example_metric]["delta"] is not None]
    eligible.sort(key=lambda c: c["metrics"][example_metric]["delta"])
    for title, cases in (
        (
            "退化样例",
            [c for c in eligible if c["metrics"][example_metric]["delta"] < 0][:10],
        ),
        (
            "改进样例",
            [c for c in reversed(eligible) if c["metrics"][example_metric]["delta"] > 0][:10],
        ),
    ):
        lines += ["", f"## {title}（{example_metric}）", ""]
        lines += [
            f"- {c['case_id']} ({c['metrics'][example_metric]['delta']:+.4f})：{c['query']}"
            for c in cases
        ] or ["无。"]
    lines += ["", "完整逐题差值见同名 JSON。工程门槛不代表统计显著性。"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
