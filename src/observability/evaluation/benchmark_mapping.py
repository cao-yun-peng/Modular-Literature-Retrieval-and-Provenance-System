"""Resolve human-verified source-to-index alternatives without changing gold spans."""

from __future__ import annotations

import copy

from .benchmark_data import digest


def mapped_cases(data: dict, index: dict, mapping: dict | None) -> list[dict]:
    if not mapping:
        return data["test_cases"]
    if mapping.get("dataset_id") != digest(data) or mapping.get("index_id") != index["index_id"]:
        raise ValueError("Evidence mapping is bound to another dataset/index")
    chunks = {c["chunk_id"]: c for c in index["chunks"]}
    cases = copy.deepcopy(data["test_cases"])
    ids = {c["id"] for c in cases}
    if not set(mapping.get("cases", {})) <= ids:
        raise ValueError("Mapping contains an unknown question")
    for case in cases:
        spans = {
            digest(s): s for g in case["evidence_groups"] for a in g["alternatives"] for s in a
        }
        resolved = {}
        for entry in mapping.get("cases", {}).get(case["id"], []):
            if entry["span_id"] not in spans:
                raise ValueError("Mapping refers to an unknown source span")
            if entry.get("review_status") != "approved":
                continue
            if not entry.get("reviewer") or not entry.get("reviewed_at"):
                raise ValueError("Approximate evidence mapping requires documented human review")
            alternatives = entry.get("approved_alternatives", [])
            if not alternatives:
                raise ValueError("Approved mapping needs explicit chunk text segments")
            pid = spans[entry["span_id"]]["paper_id"]
            translated = []
            for alternative in alternatives:
                if not alternative:
                    raise ValueError("Empty mapping alternative")
                segments = []
                for segment in alternative:
                    chunk = chunks.get(segment["chunk_id"])
                    if not chunk or chunk.get("paper_id") != pid:
                        raise ValueError("Mapping chunk belongs to another source paper")
                    start, end = segment["start"], segment["end"]
                    if (
                        type(start) is not int
                        or type(end) is not int
                        or not 0 <= start < end <= len(chunk["text"])
                        or end - start < 20
                    ):
                        raise ValueError(
                            "Invalid mapping offsets (at least 20 characters required)"
                        )
                    segments.append({"paper_id": pid, "quote": chunk["text"][start:end]})
                translated.append(segments)
            resolved[entry["span_id"]] = translated
        case["_mapped_spans"] = resolved
    return cases
