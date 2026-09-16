"""Exercise the HTTP API with real services in an isolated validation collection."""

import argparse
import json
import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--fresh-api",
        action="store_true",
        help="Use a new isolated runtime, including a fresh MinerU cache",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--retry-key",
        default="validation-recovery",
        help="Recovery attempt identifier; change only for a deliberate additional retry",
    )
    parser.add_argument("--answer-all", action="store_true")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry each failed task once, reusing evidence for answer failures",
    )
    args = parser.parse_args()
    from fastapi.testclient import TestClient

    from src.core.settings import load_settings
    from src.web_api.app import create_app

    output = args.output or ROOT / (
        "output/web-validation-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        if args.fresh_api
        else "output/web-validation"
    )
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    print("Validation output:", output, flush=True)
    settings = load_settings()
    settings = replace(
        settings,
        vector_store=replace(
            settings.vector_store,
            persist_directory=str(output / "runtime/chroma"),
            collection_name="web_live_validation",
        ),
    )

    def save(name, data):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Redirect only filesystem isolation; the real Pipeline and all model clients
    # execute unchanged. Existing successful caches and indexes are not removed.
    from src.ingestion.pipeline import IngestionPipeline

    class IsolatedPipeline(IngestionPipeline):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw, runtime_root=output / "runtime")

    isolation = (
        patch("src.ingestion.pipeline.IngestionPipeline", IsolatedPipeline)
        if args.fresh_api
        else nullcontext()
    )
    with isolation, TestClient(create_app(settings, output / "runtime/web")) as client:
        recovered = []

        def wait(rid, allow_retry=True):
            deadline = time.monotonic() + 900
            last = None
            while time.monotonic() < deadline:
                run = client.get(f"/api/v1/runs/{rid}").json()
                if run["stage"] != last:
                    print(run["kind"], run["stage"], flush=True)
                    last = run["stage"]
                if run["status"] in ("succeeded", "failed", "interrupted"):
                    save(rid + ".json", run)
                    if run["status"] != "succeeded" and args.retry_failed and allow_retry:
                        retry = client.post(
                            f"/api/v1/runs/{rid}/retry",
                            json={"stage": "answer" if run["stage"] == "answer" else "auto"},
                            headers={"Idempotency-Key": f"{args.retry_key}-{rid}"},
                        )
                        assert retry.status_code == 202, retry.text
                        recovered.append(
                            {
                                "failed_run": rid,
                                "retry_run": retry.json()["id"],
                                "stage": run["stage"],
                                "error": run.get("error"),
                            }
                        )
                        return wait(retry.json()["id"], False)
                    assert run["status"] == "succeeded", run.get("error")
                    return run
                time.sleep(0.3)
            raise TimeoutError(rid)

        doc = client.post(
            "/api/v1/documents/upload",
            files={"file": (args.pdf.name, args.pdf.read_bytes(), "application/pdf")},
        )
        assert doc.status_code == 200, doc.text
        doc = doc.json()
        res = client.post(
            "/api/v1/ingestion-runs",
            json={"document_id": doc["id"]},
            headers={"Idempotency-Key": "live-ingestion-v1"},
        )
        assert res.status_code == 202, res.text
        ingestion = wait(res.json()["id"])
        chunks = client.get(f"/api/v1/runs/{ingestion['id']}/chunks?limit=100").json()["items"]
        assert chunks and max(c["token_count"] for c in chunks) <= 2500
        if not args.fresh_api:
            assert len(chunks) == 15
            assert max(c["token_count"] for c in chunks) == 2316
        if args.fresh_api:
            assert ingestion["result"]["parser_cache_hit"] is False
        assert all(c["text"] is None for c in chunks)
        detail = client.get(f"/api/v1/chunks/{chunks[0]['id']}?run_id={ingestion['id']}").json()
        assert detail["text"] and "source_path" not in detail["metadata"]
        events = client.get(f"/api/v1/runs/{ingestion['id']}/event-history").json()
        save("ingestion-events.json", events)
        if ingestion["source"] == "live":
            assert any(e["stage"] == "structure" for e in events)
            assert any(e.get("completed_units") == 15 for e in events)
        cases = [
            "Why is fluctuation-induced anisotropy inescapable in nonreciprocal XY models?",
            "What does Figure 3 show about active clock continuum equations?",
            "Why is the ordered phase metastable to topological defects and an aster foam?",
        ]
        results = []
        for i, query in enumerate(cases):
            response = client.post(
                "/api/v1/retrieval-runs",
                json={
                    "query": query,
                    "document_ids": [doc["id"]],
                    "top_k": 5,
                    "generate_answer": args.answer_all or i == 0,
                },
                headers={"Idempotency-Key": f"live-query-v1-{i}"},
            )
            assert response.status_code == 202, response.text
            run = wait(response.json()["id"])
            result = client.get(f"/api/v1/retrieval-runs/{run['id']}/result").json()
            assert len(result["evidence"]) == 5, result
            assert all(
                e["document_id"] == doc["id"] and e["run_id"] == ingestion["id"]
                for e in result["evidence"]
            )
            if args.answer_all or i == 0:
                assert result["answer_status"] == "succeeded" and result["answer"]
            else:
                assert result["answer_status"] == "not_requested"
            save(f"query-{i + 1}.json", result)
            results.append(
                {
                    "query": query,
                    "evidence_count": len(result["evidence"]),
                    "answer_status": result["answer_status"],
                }
            )
        summary = {
            "success": True,
            "document_id": doc["id"],
            "run_id": ingestion["id"],
            "chunks": len(chunks),
            "max_tokens": max(c["token_count"] for c in chunks),
            "figure_chunks": sum(c["chunk_type"] == "figure" for c in chunks),
            "fresh_api": args.fresh_api,
            "parser_cache_hit": ingestion["result"].get("parser_cache_hit"),
            "storage": ingestion["result"].get("storage"),
            "historical_match": len(chunks) == 15 and max(c["token_count"] for c in chunks) == 2316,
            "queries": results,
            "recovered_runs": recovered,
        }
        from src.libs.vector_store.chroma_store import ChromaStore

        store = ChromaStore(settings, collection_name=settings.vector_store.collection_name)
        vectors = store.collection.get(include=["embeddings"])["embeddings"]
        assert len(vectors) == len(chunks) and all(len(v) == 1024 for v in vectors)
        summary.update(vector_count=len(vectors), embedding_dimensions=1024)
        save("summary.json", summary)
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
