"""Versioned paper corpus, source anchors and human-review gates (no model imports)."""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

SCHEMA = "paper-benchmark/1.0"
QUOTAS = {
    "concept": 30,
    "method": 24,
    "result": 18,
    "cross_paper": 18,
    "locator": 18,
    "unanswerable": 12,
}


def digest(value: Any) -> str:
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def normalize(text: str) -> str:
    """Stable Unicode/line-wrap normalization; preserve mathematical signs/numbers."""
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def prepare_corpus(source: Path, output: Path, family_overrides: dict | None = None) -> dict:
    """Read PDF signatures (not extensions), retain physical-page source text."""
    import fitz

    if (output / "corpus.json").exists():
        raise ValueError("Corpus already exists; use a new version directory")
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents:
        raise ValueError("Output must be outside the original corpus")
    records: dict[str, dict] = {}
    rejected = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"%PDF-" not in raw[:1024]:
            rejected.append({"path": str(path), "status": "not_pdf"})
            continue
        sha = digest(raw)
        relative = path.relative_to(source)
        if sha in records:
            records[sha]["sources"].append(str(path))
            records[sha]["topics"] = sorted(set(records[sha]["topics"] + [relative.parts[0]]))
            continue
        record = {
            "paper_id": sha,
            "sha256": sha,
            "sources": [str(path)],
            "topics": [relative.parts[0] if len(relative.parts) > 1 else "other"],
            "title": path.stem,
            "doi": None,
            "status": "error",
        }
        try:
            document = fitz.open(stream=raw, filetype="pdf")
            pages = [
                {"page": i + 1, "text": normalize(page.get_text(sort=False))}
                for i, page in enumerate(document)
            ]
            title = str(document.metadata.get("title") or "").strip()
            if len(title) > 12 and not title.lower().startswith(("microsoft", "untitled")):
                record["title"] = title
            # Do not extract a random cited DOI from the references pages.
            doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", pages[0]["text"], re.I)
            record["doi"] = doi.group(0).rstrip(".,;)").lower() if doi else None
            record.update(
                status="parsed",
                page_count=len(pages),
                low_text_pages=[p["page"] for p in pages if len(p["text"]) < 50],
                pages_file=f"pages/{sha}.json",
                pdf_file=f"pdfs/{sha}.pdf",
            )
            write_json(output / record["pages_file"], pages)
            record["pages_sha256"] = digest(pages)
            (output / "pdfs").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output / record["pdf_file"])
            document.close()
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        records[sha] = record
    if not records:
        raise ValueError("No PDFs found")
    papers = list(records.values())
    # Group DOI, normalized title, then explicit body/supplement overrides.
    parent = {p["paper_id"]: p["paper_id"] for p in papers}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    overrides = family_overrides or {}
    for i, a in enumerate(papers):
        for b in papers[:i]:
            at, bt = normalize(a["title"]).lower(), normalize(b["title"]).lower()
            same = bool(a["doi"] and a["doi"] == b["doi"])
            same |= len(at) > 25 and SequenceMatcher(None, at, bt).ratio() >= 0.94
            same |= a["paper_id"] in overrides and overrides[a["paper_id"]] == overrides.get(
                b["paper_id"]
            )
            if same:
                parent[find(a["paper_id"])] = find(b["paper_id"])
    groups = defaultdict(list)
    for p in papers:
        groups[find(p["paper_id"])].append(p)
    strata = defaultdict(list)
    for group in groups.values():
        strata[min(p["topics"][0] for p in group)].append(group)
    rng = random.Random(42)
    sizes = Counter()
    for topic, grouped in sorted(strata.items()):
        rng.shuffle(grouped)
        local = Counter()
        for group in grouped:
            split = min(("dev", "test"), key=lambda s: (local[s], sizes[s], s))
            family = min(p["paper_id"] for p in group)
            for p in group:
                p.update(family_id=family, split=split)
            sizes[split] += len(group)
            local[split] += len(group)
    payload = {
        "schema_version": SCHEMA,
        "created_at": now(),
        "seed": 42,
        "source_root": str(source),
        "papers": papers,
        "rejected_files": rejected,
        "family_review": {"status": "pending", "reviewer": None},
        "split_counts": dict(sizes),
        "normalization": "nfkc-linewrap-whitespace-v1",
    }
    payload["corpus_id"] = corpus_identity(payload)
    write_json(output / "corpus.json", payload)
    return payload


def corpus_identity(corpus: dict) -> str:
    return digest(
        [
            {k: p.get(k) for k in ("paper_id", "family_id", "split", "pages_file")}
            for p in sorted(corpus["papers"], key=lambda p: p["paper_id"])
        ]
    )


def load_corpus(root: Path) -> tuple[dict, dict]:
    corpus = read_json(root / "corpus.json")
    if corpus["corpus_id"] != corpus_identity(corpus):
        raise ValueError("Corpus grouping/split identity changed; prepare a new version")
    pages = {
        p["paper_id"]: read_json(root / p["pages_file"])
        for p in corpus["papers"]
        if p["status"] == "parsed"
    }
    for paper in corpus["papers"]:
        if paper.get("pages_sha256") and digest(pages[paper["paper_id"]]) != paper["pages_sha256"]:
            raise ValueError("Canonical source text changed")
    return corpus, pages


def validate_dataset(data: dict, corpus: dict, pages: dict, *, freeze: bool = False) -> None:
    if data.get("schema_version") != SCHEMA or data.get("corpus_id") != corpus["corpus_id"]:
        raise ValueError("Dataset schema/corpus mismatch")
    if data.get("source_text_id") and data["source_text_id"] != digest(pages):
        raise ValueError("Dataset source text version mismatch")
    papers = {p["paper_id"]: p for p in corpus["papers"]}
    cases = data.get("test_cases", [])
    if not cases:
        raise ValueError("Dataset is empty")
    ids, questions = set(), []
    for case in cases:
        cid = case.get("id")
        if not cid or cid in ids or not str(case.get("query", "")).strip():
            raise ValueError(f"Missing/duplicate ID or empty query: {cid}")
        ids.add(cid)
        if case.get("language") not in ("en", "zh") or case.get("split") not in ("dev", "test"):
            raise ValueError(f"Invalid language/split: {cid}")
        if case.get("query_type") not in QUOTAS or not isinstance(case.get("answerable"), bool):
            raise ValueError(f"Invalid query type/answerability: {cid}")
        groups = case.get("evidence_groups", [])
        if case["answerable"] and not groups:
            raise ValueError(f"Answerable case lacks evidence groups: {cid}")
        group_ids = set()
        supporting = set(case.get("source_paper_ids", []))
        for group in groups:
            if not group.get("id") or group["id"] in group_ids or not group.get("alternatives"):
                raise ValueError(f"Invalid evidence group: {cid}")
            group_ids.add(group["id"])
            for alternative in group["alternatives"]:
                if not alternative:
                    raise ValueError(f"Empty alternative: {cid}")
                for span in alternative:
                    pid = span["paper_id"]
                    supporting.add(pid)
                    page, start, end = span["page"], span["start"], span["end"]
                    if (
                        pid not in pages
                        or isinstance(page, bool)
                        or not isinstance(page, int)
                        or not 1 <= page <= len(pages[pid])
                    ):
                        raise ValueError(f"Invalid source page: {cid}")
                    text = pages[pid][page - 1]["text"]
                    if not (
                        isinstance(start, int)
                        and isinstance(end, int)
                        and 0 <= start < end <= len(text)
                        and text[start:end] == span["quote"]
                    ):
                        raise ValueError(f"Quote/offset mismatch: {cid}")
        if not supporting or any(p not in papers for p in supporting):
            raise ValueError(f"Missing/unknown source papers: {cid}")
        if any(papers[p]["split"] != case["split"] for p in supporting):
            raise ValueError(f"Paper family leaks across splits: {cid}")
        if case["query_type"] == "cross_paper" and len(supporting) < 2:
            raise ValueError(f"Cross-paper question has fewer than two papers: {cid}")
        q = normalize(case["query"]).lower()
        for prior, split in questions:
            if q == prior or (
                split != case["split"] and SequenceMatcher(None, q, prior).ratio() > 0.9
            ):
                raise ValueError(f"Duplicate/near-duplicate question: {cid}")
        questions.append((q, case["split"]))
        if freeze:
            review = case.get("review", {})
            if (
                review.get("status") != "approved"
                or not review.get("reviewer")
                or not review.get("reviewed_at")
            ):
                raise ValueError(f"Human review missing: {cid}")
    if freeze:
        if data.get("source_text_id") != digest(pages):
            raise ValueError("Frozen labels require the canonical source text identity")
        family_review = corpus.get("family_review", {})
        if (
            family_review.get("status") != "approved"
            or not family_review.get("reviewer")
            or not family_review.get("reviewed_at")
        ):
            raise ValueError("Paper-family human review is required before freeze")
        if len(cases) != 120 or Counter(c["query_type"] for c in cases) != Counter(QUOTAS):
            raise ValueError("Final dataset must meet all 120-question type quotas")
        pilot = [c for c in cases if c.get("pilot")]
        if len(pilot) != 20 or any(c["split"] != "dev" for c in pilot):
            raise ValueError("Exactly 20 development pilot cases must be retained")
        if any(c.get("slot_id") for c in cases):
            slots = [c.get("slot_id") for c in cases]
            if len(set(slots)) != 120 or None in slots:
                raise ValueError("Select exactly one candidate per question slot")
        for split in ("dev", "test"):
            subset = [c for c in cases if c["split"] == split]
            if Counter(c["language"] for c in subset) != Counter(en=42, zh=18):
                raise ValueError("Each split requires 42 English and 18 Chinese questions")
            if Counter(c["query_type"] for c in subset) != Counter(
                {k: v // 2 for k, v in QUOTAS.items()}
            ):
                raise ValueError("Each split must contain half of each question type")
        second = [
            c
            for c in cases
            if c.get("second_review", {}).get("status") == "approved"
            and c["second_review"].get("reviewer")
            and c["second_review"].get("reviewed_at")
            and c["second_review"].get("blind") is True
        ]
        if len(second) < 24:
            raise ValueError("24 documented second reviews required")


def anchor(paper_id: str, page: int, text: str, quote: str) -> dict:
    quote = normalize(quote)
    start = text.find(quote)
    if start < 0 or len(quote) < 20:
        raise ValueError("Quote not found verbatim in supplied normalized page")
    return {
        "paper_id": paper_id,
        "page": page,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "section": None,
    }


def index_identity(chunks: list[dict]) -> str:
    return digest(sorted(chunks, key=lambda c: c["chunk_id"]))


def map_evidence(data: dict, index: dict) -> dict:
    """Exact containment is a mapping proposal, never a relevance judgment."""
    if index["corpus_id"] != data["corpus_id"] or index["index_id"] != index_identity(
        index["chunks"]
    ):
        raise ValueError("Index/corpus identity mismatch")
    mapped = {}
    for case in data["test_cases"]:
        spans = [s for g in case["evidence_groups"] for a in g["alternatives"] for s in a]
        entries = []
        for span in spans:
            quote = normalize(span["quote"])
            exact, partial = [], []
            for chunk in index["chunks"]:
                if chunk.get("paper_id") != span["paper_id"]:
                    continue
                text = normalize(chunk["text"])
                if quote in text:
                    exact.append(chunk["chunk_id"])
                elif len(text) >= 20 and (
                    text in quote
                    or SequenceMatcher(None, quote, text, autojunk=False).find_longest_match().size
                    >= min(40, len(quote))
                ):
                    partial.append(chunk["chunk_id"])
            entries.append(
                {
                    "span_id": digest(span),
                    "exact_candidates": exact,
                    "partial_candidates": partial,
                    "review_status": "pending",
                    "reviewer": None,
                    "reviewed_at": None,
                    "approved_alternatives": [],
                    "status": "exact" if exact else "partial" if partial else "missing",
                }
            )
        mapped[case["id"]] = entries
    return {
        "schema_version": SCHEMA,
        "dataset_id": digest(data),
        "index_id": index["index_id"],
        "corpus_id": data["corpus_id"],
        "cases": mapped,
    }


def source_diagnostics(corpus: dict, pages: dict, data: dict) -> dict:
    damaged = []
    for paper in corpus["papers"]:
        for page in pages.get(paper["paper_id"], []):
            count = page["text"].count("\ufffd")
            if count or len(page["text"]) < 50:
                damaged.append(
                    {
                        "paper_id": paper["paper_id"],
                        "title": paper["title"],
                        "page": page["page"],
                        "replacement_characters": count,
                        "text_characters": len(page["text"]),
                    }
                )
    cases = []
    for case in data["test_cases"]:
        spans = [s for g in case["evidence_groups"] for a in g["alternatives"] for s in a]
        bad = [s for s in spans if "\ufffd" in s["quote"]]
        if bad:
            cases.append({"case_id": case["id"], "status": "needs_pdf_review", "spans": bad})
    return {
        "corpus_id": corpus["corpus_id"],
        "dataset_id": digest(data),
        "source_files": sum(len(p["sources"]) for p in corpus["papers"])
        + len(corpus.get("rejected_files", [])),
        "unique_pdfs": len(corpus["papers"]),
        "physical_pages": sum(len(p) for p in pages.values()),
        "flagged_pages": damaged,
        "flagged_cases": cases,
        "note": "Text extraction diagnostics only. Visual-only figures and corrupted formula glyphs require original-PDF review; no labels are removed.",
    }
