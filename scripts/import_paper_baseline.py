"""Prepare, import, freeze and verify the 48-paper workbench baseline.

Only import calls the running workbench, which submits PDFs to MinerU and text
to configured DashScope embeddings. Prepare/freeze/verify make no model calls.
"""
import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.baseline_guard import baseline_marker
from src.core.settings import get_bm25_index_dir, load_settings, resolve_path
from src.libs.loader.mineru_agent import MinerUAgentClient
from src.web_api.store import Registry, default_workbench_root, now

BASELINE = ROOT / "data/baselines/papers48-20260916"
CORPUS = ROOT / "data/paper_benchmark/v1"
WEB_ROOT = default_workbench_root(load_settings())


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sanitized(value):
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower() in {"api_key", "password", "secret", "token", "access_token"}
                    else sanitized(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitized(v) for v in value]
    return str(value) if isinstance(value, Path) else value


def code_hashes():
    paths = sorted((ROOT / "src").rglob("*.py")) + [Path(__file__), ROOT / "config/settings.yaml", ROOT / "uv.lock"]
    return {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in paths}


def prepare():
    import fitz

    settings = load_settings()
    corpus = read(CORPUS / "corpus.json")
    assert len(corpus["papers"]) == 48
    parser = MinerUAgentClient(ROOT / "data/mineru-agent")
    documents, checked_pages = [], 0
    for paper in corpus["papers"]:
        path = CORPUS / paper["pdf_file"]
        assert sha(path) == paper["sha256"], path
        parts = parser.prepare_parts(path)
        assert parts[0]["page_start"] == 1 and parts[-1]["page_end"] == paper["page_count"]
        assert sum(p["page_end"] - p["page_start"] + 1 for p in parts) == paper["page_count"]
        segmented = len(parts) > 1 or Path(parts[0]["path"]) != path.resolve()
        if segmented:
            with fitz.open(path) as original:
                for part in parts:
                    with fitz.open(part["path"]) as pdf:
                        assert len(pdf) <= 20 and Path(part["path"]).stat().st_size <= 10 * 1024 * 1024
                        for i, page in enumerate(pdf):
                            source = original[part["page_start"] - 1 + i]
                            assert page.get_text() == source.get_text(), (path, i)
                            assert page.get_pixmap(matrix=fitz.Matrix(.5, .5)).samples == source.get_pixmap(matrix=fitz.Matrix(.5, .5)).samples, (path, i)
                            checked_pages += 1
        documents.append(dict(sha256=paper["sha256"], title=paper["title"],
                              source_path=str(path.resolve()), pages=paper["page_count"],
                              bytes=path.stat().st_size, segmented=segmented, parts=parts))
    assert len({p["sha256"] for p in documents}) == 48
    plan = dict(baseline_id=BASELINE.name, created_at=now(), status="prepared",
                corpus_sha256=sha(CORPUS / "corpus.json"), document_count=48,
                page_count=sum(p["pages"] for p in documents),
                source_bytes=sum(p["bytes"] for p in documents),
                parser_request_count=sum(len(p["parts"]) for p in documents),
                segmented_documents=sum(p["segmented"] for p in documents),
                lossless_render_verified_pages=checked_pages,
                destinations={"pdf_parser": MinerUAgentClient.base_url,
                              "text_embedding": settings.embedding.base_url,
                              "embedding_model": settings.embedding.model},
                settings=sanitized(asdict(settings)), code_hashes=code_hashes(), documents=documents)
    old = BASELINE / "plan.json"
    if old.exists() and (BASELINE / "checkpoint.json").exists():
        assert read(old)["code_hashes"] == plan["code_hashes"], "Code changed after import started"
    write(old, plan)
    print(json.dumps({k: v for k, v in plan.items() if k not in {"documents", "settings", "code_hashes"}}, ensure_ascii=True), flush=True)


def import_all():
    import httpx

    plan = read(BASELINE / "plan.json")
    assert plan["code_hashes"] == code_hashes(), "Prepare again after code changes"
    settings = load_settings()
    assert plan["settings"] == sanitized(asdict(settings)), "Configuration changed"
    checkpoint = read(BASELINE / "checkpoint.json") if (BASELINE / "checkpoint.json").exists() else {}
    expected = {p["sha256"] for p in plan["documents"]}
    with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=90) as client:
        def api(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        config = api("GET", "/api/v1/config/public")
        assert config["max_pages"] == 200 and config["max_file_bytes"] == 50 * 1024 * 1024
        assert config["embedding_model"] == settings.embedding.model and config["embedding_dimensions"] == 1024
        assert config["chunk_limit"] == 2500 and config["target_overlap"] == 200
        existing = api("GET", "/api/v1/documents?limit=100")
        assert not existing["next_cursor"]
        assert {d["content_hash"] for d in existing["items"]} <= expected, "Collection contains papers outside the requested corpus"
        for i, paper in enumerate(plan["documents"], 1):
            path = Path(paper["source_path"])
            assert sha(path) == paper["sha256"]
            print(f"[{i}/48] {paper['sha256'][:12]} {paper['pages']} pages", flush=True)
            document = api("POST", "/api/v1/documents/upload", files={"file": (paper["title"][:120].replace("/", "-") + ".pdf", path.read_bytes(), "application/pdf")})
            run = api("POST", "/api/v1/ingestion-runs", json={"document_id": document["id"]},
                      headers={"Idempotency-Key": plan["baseline_id"] + "-" + paper["sha256"]})
            checkpoint[paper["sha256"]] = dict(document_id=document["id"], run_id=run["id"], status=run["status"])
            write(BASELINE / "checkpoint.json", checkpoint)
            deadline, previous = time.monotonic() + 3600, None
            while time.monotonic() < deadline:
                run = api("GET", f"/api/v1/runs/{run['id']}")
                state = (run["status"], run["stage"])
                if state != previous:
                    print(" ", *state, flush=True)
                    previous = state
                if run["status"] in {"succeeded", "failed", "interrupted"}:
                    break
                time.sleep(2)
            checkpoint[paper["sha256"]].update(status=run["status"], result=run.get("result"), error=run.get("error"))
            write(BASELINE / "checkpoint.json", checkpoint)
            assert run["status"] == "succeeded", {"run_id": run["id"], "error": run.get("error")}
            print("  chunks:", run["result"]["chunk_count"], flush=True)
    print("All 48 documents imported. Freeze is a separate verified operation.", flush=True)


def freeze():
    from src.libs.vector_store.chroma_store import ChromaStore

    plan, settings = read(BASELINE / "plan.json"), load_settings()
    assert plan["code_hashes"] == code_hashes(), "Code changed during import"
    assert plan["settings"] == sanitized(asdict(settings))
    marker = baseline_marker(settings, settings.vector_store.collection_name)
    if marker.exists():
        raise RuntimeError("Baseline marker already exists; run verify")
    registry = Registry(WEB_ROOT)
    marker.parent.mkdir(parents=True, exist_ok=True)
    # Prevent new ingestion requests before checking and copying the completed state.
    with marker.open("x", encoding="utf-8") as handle:
        json.dump({"status": "freezing", "baseline_id": BASELINE.name}, handle)
    try:
        assert not [r for r in registry.all("runs") if r["kind"] == "ingestion" and r["status"] in {"queued", "running"}]
        assert registry.health(settings.vector_store.collection_name)["state"] == "ready"
        documents = registry.all("documents")
        assert len(documents) == 48 and {d["content_hash"] for d in documents} == {p["sha256"] for p in plan["documents"]}
        chunks, records = [], []
        for document in sorted(documents, key=lambda d: d["id"]):
            assert document["status"] == "succeeded"
            rid = document["latest_successful_run_id"]
            run = registry.get("runs", rid)
            assert run["status"] == "succeeded"
            rows = registry.chunks(rid)
            assert len(rows) == document["chunk_count"] > 0
            assert all(c["text"].strip() and 0 < c["token_count"] <= 2500 for c in rows)
            chunks.extend(rows)
            source = Path(document["source_path"])
            assert sha(source) == document["content_hash"]
            target = BASELINE / "pdfs" / (document["content_hash"] + ".pdf")
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            artifact_dir = BASELINE / "runs" / rid
            artifact_dir.mkdir(parents=True, exist_ok=True)
            for name in ("raw.md", "normalized.md", "structure.json", "document.json", "chunks.json", "ingestion.json", "trace.json"):
                src = WEB_ROOT / "runs" / rid / name
                assert src.is_file(), src
                shutil.copy2(src, artifact_dir / name)
            records.append(dict(document_id=document["id"], sha256=document["content_hash"], run_id=rid,
                                title=document["title"], page_count=document["page_count"], chunks=len(rows)))
        chunk_ids = {c["id"] for c in chunks}
        assert len(chunk_ids) == len(chunks)
        store = ChromaStore(settings)
        raw = store.collection.get(include=["documents", "metadatas", "embeddings"])
        assert set(raw["ids"]) == chunk_ids, "Vector/chunk identity mismatch"
        vectors = []
        for i, cid in enumerate(raw["ids"]):
            vector = list(map(float, raw["embeddings"][i]))
            assert len(vector) == 1024 and all(math.isfinite(v) for v in vector)
            vectors.append(dict(id=cid, document=raw["documents"][i], metadata=raw["metadatas"][i], embedding=vector))
        write(BASELINE / "vectors.json", sorted(vectors, key=lambda x: x["id"]))
        write(BASELINE / "chunks.json", sorted(chunks, key=lambda x: x["id"]))
        sparse_path = get_bm25_index_dir(settings.vector_store.collection_name, settings) / (settings.vector_store.collection_name + "_bm25.json")
        sparse = read(sparse_path)
        sparse_ids = {p["chunk_id"] for entry in sparse["index"].values() for p in entry["postings"]}
        assert sparse_ids == chunk_ids, "BM25/chunk identity mismatch"
        shutil.copy2(sparse_path, BASELINE / "bm25.json")
        with sqlite3.connect(registry.path) as original, sqlite3.connect(BASELINE / "workbench.sqlite3") as backup:
            original.backup(backup)
        write(BASELINE / "settings.json", plan["settings"])
        write(BASELINE / "documents.json", records)
        manifest = dict(baseline_id=BASELINE.name, status="frozen", frozen_at=now(),
                        document_count=48, page_count=sum(r["page_count"] for r in records),
                        chunk_count=len(chunks), vector_count=len(vectors), embedding_dimensions=1024,
                        max_chunk_tokens=max(c["token_count"] for c in chunks),
                        collection=settings.vector_store.collection_name,
                        quality_evaluation="not_yet_evaluated", code_hashes=plan["code_hashes"],
                        files={str(p.relative_to(BASELINE)).replace("\\", "/"): sha(p) for p in sorted(BASELINE.rglob("*")) if p.is_file() and p.name not in {"manifest.json", "checkpoint.json"}})
        write(BASELINE / "manifest.json", manifest)
        write(marker, dict(status="frozen", baseline_id=BASELINE.name,
                           manifest=str(BASELINE / "manifest.json"), manifest_sha256=sha(BASELINE / "manifest.json")))
        print(json.dumps({k: v for k, v in manifest.items() if k not in {"files", "code_hashes"}}, ensure_ascii=True), flush=True)
    except BaseException:
        # Only remove this invocation's not-yet-frozen marker; preserve all artifacts.
        if marker.exists() and read(marker).get("status") == "freezing":
            marker.unlink()
        raise


def verify():
    manifest = read(BASELINE / "manifest.json")
    mismatches = [name for name, expected in manifest["files"].items() if not (BASELINE / name).is_file() or sha(BASELINE / name) != expected]
    assert not mismatches, mismatches
    marker = read(baseline_marker(load_settings(), manifest["collection"]))
    assert marker["status"] == "frozen" and marker["manifest_sha256"] == sha(BASELINE / "manifest.json")
    print(json.dumps(dict(status="verified", files_checked=len(manifest["files"]), documents=manifest["document_count"], chunks=manifest["chunk_count"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "import", "freeze", "verify"])
    args = parser.parse_args()
    {"prepare": prepare, "import": import_all, "freeze": freeze, "verify": verify}[args.command]()
