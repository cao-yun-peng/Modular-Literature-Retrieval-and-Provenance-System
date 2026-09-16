"""Deterministic evidence metrics; unknown judgments are not negative labels."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache

from .benchmark_data import digest, normalize


@lru_cache(maxsize=65536)
def matching_ranges(quote: str, text: str) -> tuple:
    # Every match of at least 20 characters contains an entire aligned 10-char
    # window. This exact prefilter cannot remove an eligible matching block.
    if not any(quote[i : i + 10] in text for i in range(0, len(quote) - 9, 10)):
        return ()
    return tuple(
        (b.a, b.size)
        for b in SequenceMatcher(None, quote, text, autojunk=False).get_matching_blocks()
        if b.size >= 20
    )


def span_covered(span: dict, evidence: list[dict]) -> bool:
    quote = normalize(span["quote"])
    coverage = [False] * len(quote)
    for item in evidence:
        if item.get("paper_id") != span["paper_id"]:
            continue
        text = normalize(item.get("text", ""))
        if quote in text:
            return True
        # A span crossing a chunk boundary can be covered by several chunks.
        # Require exact coverage of every non-whitespace character; numbers and
        # mathematical symbols may never disappear behind a fuzzy threshold.
        for start, size in matching_ranges(quote, text):
            coverage[start : start + size] = [True] * size
    return all(hit or char.isspace() for hit, char in zip(coverage, quote))


def coverage(case: dict, evidence: list[dict]) -> tuple[float | None, list[str]]:
    groups = case.get("evidence_groups", [])
    if not case["answerable"] or not groups:
        return None, []

    def supported(span):
        return span_covered(span, evidence) or any(
            all(span_covered(segment, evidence) for segment in alternative)
            for alternative in case.get("_mapped_spans", {}).get(digest(span), [])
        )

    hit = [
        g["id"]
        for g in groups
        if any(all(supported(s) for s in alternative) for alternative in g["alternatives"])
    ]
    return len(hit) / len(groups), hit


def expanded(evidence: list[dict], budget: int | None = None) -> list[dict]:
    result = []
    remaining = budget
    for item in evidence:
        context = item.get("expanded_context") or {}
        if isinstance(context, str):
            context = {"text": context}
        for text in (item.get("text", ""), context.get("text", "")):
            if remaining is not None:
                text = text[: max(0, remaining)]
                remaining -= len(text)
            if text:
                result.append({"paper_id": item.get("paper_id"), "text": text})
        for annotation in item.get("image_annotations", []):
            text = annotation.get("description", "")
            if remaining is not None:
                text = text[:max(0, remaining)]
                remaining -= len(text)
            if text:
                # Returned visual annotations consume budget but are not PDF gold evidence.
                result.append({"paper_id": None, "text": text})
    return result


def ranking(evidence: list[dict], judgments: dict, k: int) -> dict:
    seen, grades = set(), []
    for item in evidence[:k]:
        cid = item["chunk_id"]
        row = judgments.get(cid, {})
        valid = (
            type(row.get("grade")) is int
            and row.get("grade") in (0, 1, 2)
            and row.get("reviewer")
            and row.get("reviewed_at")
        )
        grades.append((0 if cid in seen else row["grade"]) if valid else None)
        seen.add(cid)
    judged = sum(g is not None for g in grades)
    result = {
        "judged_at_k": judged / len(grades) if grades else 1.0,
        "mrr_at_k": None,
        "direct_mrr_at_k": None,
        "ndcg_at_k": None,
        "direct_support_fraction": None,
        "background_fraction": None,
        "irrelevant_fraction": None,
    }
    if any(g is None for g in grades):
        return result
    for name, grade in (
        ("direct_support_fraction", 2),
        ("background_fraction", 1),
        ("irrelevant_fraction", 0),
    ):
        result[name] = sum(g == grade for g in grades) / len(grades) if grades else None
    relevant = [
        row["grade"]
        for row in judgments.values()
        if row.get("grade") in (1, 2) and row.get("reviewer") and row.get("reviewed_at")
    ]
    if not relevant:
        return result
    result["mrr_at_k"] = next((1 / i for i, g in enumerate(grades, 1) if g > 0), 0.0)
    result["direct_mrr_at_k"] = next((1 / i for i, g in enumerate(grades, 1) if g == 2), 0.0)
    # Linear graded gain, matching trec_eval/BEIR nDCG (not exponential gain).
    ideal = sum(g / math.log2(i + 1) for i, g in enumerate(sorted(relevant, reverse=True)[:k], 1))
    actual = sum(g / math.log2(i + 1) for i, g in enumerate(grades, 1))
    result["ndcg_at_k"] = actual / ideal if ideal else None
    return result


def score_case(
    case: dict,
    response: dict,
    pages: dict,
    judgments: dict,
    indexed_chunks: list[dict],
    k: int,
    budget: int = 12000,
) -> dict:
    evidence = response.get("evidence", [])[:k]
    scores = ranking(evidence, judgments, k)
    base, hit = coverage(case, evidence)
    total, total_hit = coverage(case, expanded(evidence))
    limited, _ = coverage(case, expanded(evidence, budget))
    retained, _ = coverage(case, indexed_chunks)
    supporting = {
        s["paper_id"] for g in case["evidence_groups"] for a in g["alternatives"] for s in a
    }
    scores.update(
        evidence_recall_at_k=base,
        all_evidence_at_k=float(base == 1) if base is not None else None,
        document_hit_at_k=float(any(e.get("paper_id") in supporting for e in evidence))
        if case["answerable"]
        else None,
        expanded_evidence_recall=total,
        budgeted_evidence_recall=limited,
        expansion_gain=total - base if base is not None else None,
        evidence_retention=retained,
    )
    if not case["answerable"]:
        scores["mrr_at_k"] = scores["direct_mrr_at_k"] = scores["ndcg_at_k"] = None
    traceable = locatable = correct_pages = known_pages = 0
    for item in evidence:
        pid, text = item.get("paper_id"), normalize(item.get("text", ""))
        source_pages = pages.get(pid, [])
        # Source identity alone is insufficient: also verify returned text.
        source = " ".join(p["text"] for p in source_pages)
        authentic = bool(text and text in source)
        traceable += int(authentic)
        start, end = item.get("page_start"), item.get("page_end")
        valid = type(start) is int and type(end) is int and 1 <= start <= end <= len(source_pages)
        if valid:
            locatable += 1
            located = " ".join(p["text"] for p in source_pages[start - 1 : end])
            correct_pages += int(bool(text and text in located))
        if start is not None or end is not None:
            known_pages += 1
    n = len(evidence)
    unique = {(e.get("paper_id"), normalize(e.get("text", ""))) for e in evidence}
    scores.update(
        source_traceability=traceable / n if n else None,
        page_completeness=locatable / n if n else None,
        page_accuracy=correct_pages / known_pages if known_pages else None,
        citation_correctness=correct_pages / n if n else None,
        duplicate_rate=1 - len(unique) / n if n else None,
    )
    return {
        "metrics": scores,
        "covered_groups": hit,
        "expanded_covered_groups": total_hit,
        "metric_states": {
            key: (
                "unjudged"
                if value is None
                and key in ("mrr_at_k", "direct_mrr_at_k", "ndcg_at_k")
                and case["answerable"]
                else "not_applicable"
                if value is None
                else "scored"
            )
            for key, value in scores.items()
        },
        "characters": {
            "original": sum(len(e.get("text", "")) for e in evidence),
            "with_expansion": sum(len(e["text"]) for e in expanded(evidence)),
            "budget": budget,
        },
        "coverage_signal": response.get("coverage", {}).get("signal", "not_evaluated"),
        "fulltext_handoff": "not_covered",
    }


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def aggregate(rows: list[dict]) -> dict:
    keys = sorted({k for r in rows for k in r.get("metrics", {})})
    metrics = {}
    for key in keys:
        values = [r["metrics"][key] for r in rows if r.get("metrics", {}).get(key) is not None]
        metrics[key] = {
            "value": sum(values) / len(values) if values else None,
            "denominator": len(values),
            "total": len(rows),
            "states": dict(Counter(r.get("metric_states", {}).get(key, "missing") for r in rows)),
        }
    metrics["success_rate"] = {
        "value": sum(r["status"] == "success" for r in rows) / len(rows) if rows else None
    }
    metrics["fallback_rate"] = {
        "value": sum(bool(r.get("fallback")) for r in rows) / len(rows) if rows else None
    }
    latencies = [r["elapsed_ms"] for r in rows if r["status"] == "success"]
    phases = defaultdict(list)
    for row in rows:
        for stage in (row.get("response", {}).get("trace") or {}).get("stages", []):
            if stage.get("elapsed_ms") is not None:
                phases[stage["stage"]].append(stage["elapsed_ms"])
    tokens = [r.get("usage", {}).get("tokens", {}).get("total_tokens") for r in rows]
    return {
        "count": len(rows),
        "metrics": metrics,
        "p50_ms": percentile(latencies, 0.5),
        "p95_ms": percentile(latencies, 0.95),
        "latency_denominator": len(latencies),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "coverage_signal_counts": dict(
            Counter(r.get("coverage_signal", "not_evaluated") for r in rows)
        ),
        "phase_timings_ms": {
            name: {
                "p50": percentile(values, 0.5),
                "p95": percentile(values, 0.95),
                "count": len(values),
            }
            for name, values in phases.items()
        },
        "scoring_p95_ms": percentile([r["scoring_ms"] for r in rows if "scoring_ms" in r], 0.95),
        "recorded_llm_calls": sum(r.get("usage", {}).get("recorded_calls", 0) for r in rows),
        "recorded_total_tokens": sum(t for t in tokens if t is not None)
        if any(t is not None for t in tokens)
        else None,
        "token_usage_missing_rows": sum(t is None for t in tokens),
    }


def summarize(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    # Never average K=5 and K=10, or different strategies, into one quality score.
    for row in rows:
        prefix = f"{row['strategy']}/k={row['k']}/repeat={row.get('repeat', 1)}"
        groups[prefix].append(row)
        for key in ("language", "query_type", "split"):
            groups[f"{prefix}/{key}={row[key]}"].append(row)
        for topic in row.get("topics", []):
            groups[f"{prefix}/topic={topic}"].append(row)
        groups[f"{prefix}/cache={row.get('cache_state', 'unknown')}"].append(row)
    return {key: aggregate(value) for key, value in sorted(groups.items())}


def paired_bootstrap(
    left: list[dict], right: list[dict], metric: str, *, samples: int = 2000, seed: int = 42
) -> dict:
    """Paired resampling of connected paper-family clusters; average repeats first."""

    def by_case(rows):
        result = defaultdict(list)
        for r in rows:
            if r.get("metrics", {}).get(metric) is not None:
                result[r["case_id"]].append(r)
        return result

    a, b = by_case(left), by_case(right)
    if set(a) != set(b):
        raise ValueError("Paired comparison requires identical eligible case IDs")
    parent = {cid: cid for cid in a}

    def find(cid):
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid

    owners = {}
    for cid in sorted(a):
        for family in a[cid][0].get("family_ids", [cid]):
            if family in owners:
                parent[find(cid)] = find(owners[family])
            owners[family] = cid
    clusters = defaultdict(list)
    for cid in a:
        delta = sum(r["metrics"][metric] for r in b[cid]) / len(b[cid]) - sum(
            r["metrics"][metric] for r in a[cid]
        ) / len(a[cid])
        clusters[find(cid)].append(delta)
    blocks = list(clusters.values())
    if not blocks:
        return {"delta": None, "ci95": None, "effective_groups": 0}
    observed = [v for block in blocks for v in block]
    result = {
        "delta": sum(observed) / len(observed),
        "effective_groups": len(blocks),
        "case_count": len(observed),
        "samples": samples,
        "seed": seed,
        "ci95": None,
    }
    if len(blocks) < 2:
        result["warning"] = "Fewer than two independent paper groups; CI unavailable"
        return result
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        values = [v for _ in blocks for v in rng.choice(blocks)]
        draws.append(sum(values) / len(values))
    result["ci95"] = [percentile(draws, 0.025), percentile(draws, 0.975)]
    return result
