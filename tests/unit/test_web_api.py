import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

from src.core.settings import load_settings
from src.web_api.app import create_app
from src.web_api.service import chunk_record


@pytest.fixture
def client(tmp_path):
    settings = load_settings()
    settings = replace(
        settings,
        vector_store=replace(settings.vector_store, persist_directory=str(tmp_path / "chroma")),
    )
    with TestClient(create_app(settings, tmp_path / "web", start_worker=False)) as client:
        yield client


def pdf_bytes(pages=1):
    with fitz.open() as pdf:
        for _ in range(pages):
            pdf.new_page().insert_text((50, 50), "Paper content")
        return pdf.tobytes()


def uploaded(client):
    res = client.post(
        "/api/v1/documents/upload", files={"file": ("paper.pdf", pdf_bytes(), "application/pdf")}
    )
    assert res.status_code == 200
    return res.json()


def queued(client, doc):
    response = client.post(
        "/api/v1/ingestion-runs",
        json={"document_id": doc["id"]},
        headers={"Idempotency-Key": "ingest"},
    )
    assert response.status_code == 202
    return response.json()


def ready(client):
    doc = uploaded(client)
    run = queued(client, doc)
    service = client.app.state.workbench
    chunk = chunk_record(
        {
            "id": "chunk_1",
            "text": "Anisotropy is induced by fluctuations.",
            "metadata": {
                "source_path": "private/source.pdf",
                "token_count": 8,
                "chunk_type": "body",
                "section": "Results",
            },
        },
        run["id"],
        doc["id"],
        0,
    )
    service.registry.save_chunks(run["id"], [chunk])
    service.registry.event(run["id"], "completed", "succeeded")
    service.registry.update(
        "documents",
        doc["id"],
        status="succeeded",
        latest_successful_run_id=run["id"],
        chunk_count=1,
    )
    return doc, run


def test_config_never_contains_keys(client):
    config = client.get("/api/v1/config/public").json()
    assert config["chunk_limit"] == 2500 and config["target_overlap"] == 200
    assert config["embedding_dimensions"] == 1024
    assert all("api_key" not in k for k in config)


def test_upload_dedup_stable_pdf_and_range(client):
    raw = pdf_bytes()
    first = client.post("/api/v1/documents/upload", files={"file": ("a.pdf", raw)}).json()
    second = client.post("/api/v1/documents/upload", files={"file": ("b.pdf", raw)}).json()
    assert first["id"] == second["id"]
    assert "source_path" not in first
    response = client.get(f"/api/v1/documents/{first['id']}/source", headers={"Range": "bytes=0-4"})
    assert response.status_code == 206 and response.content == b"%PDF-"


@pytest.mark.parametrize(
    "filename,content,code",
    [
        ("file.txt", b"text", 422),
        ("file.pdf", b"%PDF-broken", 422),
        ("big.pdf", b"%PDF-" + b"x" * (50 * 1024 * 1024), 413),
    ],
    ids=["not-pdf", "broken", "too-large"],
)
def test_invalid_uploads(client, filename, content, code):
    response = client.post("/api/v1/documents/upload", files={"file": (filename, content)})
    assert response.status_code == code
    assert response.json()["error"]["code"]


def test_rejects_too_many_pages(client):
    response = client.post("/api/v1/documents/upload", files={"file": ("long.pdf", pdf_bytes(201))})
    assert response.status_code == 422


def test_long_pdf_is_one_document(client):
    response = client.post("/api/v1/documents/upload", files={"file": ("long.pdf", pdf_bytes(58))})
    assert response.status_code == 200 and response.json()["page_count"] == 58
    assert len(client.get("/api/v1/documents").json()["items"]) == 1


def test_frozen_baseline_blocks_ingestion_but_keeps_evidence_readable(client):
    from src.core.baseline_guard import baseline_marker, assert_baseline_writable

    doc, run = ready(client)
    service = client.app.state.workbench
    marker = baseline_marker(service.settings, service.collection(None))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"status":"frozen"}')
    res = client.post("/api/v1/ingestion-runs", json={"document_id": doc["id"]}, headers={"Idempotency-Key": "again"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "baseline_frozen"
    assert client.post("/api/v1/documents/upload", files={"file": ("new.pdf", pdf_bytes(2))}).status_code == 409
    assert client.get(f"/api/v1/runs/{run['id']}/chunks").status_code == 200
    with pytest.raises(RuntimeError, match="frozen baseline"):
        assert_baseline_writable(service.settings, service.collection(None))


def test_idempotency_and_parallel_duplicate_clicks(client):
    doc = uploaded(client)
    service = client.app.state.workbench
    with ThreadPoolExecutor(4) as pool:
        runs = list(
            pool.map(
                lambda i: service.create_ingestion({"document_id": doc["id"]}, f"click-{i}"),
                range(4),
            )
        )
    assert len({r["id"] for r in runs}) == 1
    assert len(service.registry.all("runs")) == 1
    response = client.post(
        "/api/v1/ingestion-runs",
        json={"document_id": doc["id"]},
        headers={"Idempotency-Key": "click-0"},
    )
    assert response.json()["id"] == runs[0]["id"]


def test_chunks_are_paginated_without_full_text(client):
    _, run = ready(client)
    response = client.get(f"/api/v1/runs/{run['id']}/chunks?limit=1").json()
    assert response["items"][0]["text"] is None
    assert response["items"][0]["metadata"] == {}
    detail = client.get(f"/api/v1/chunks/chunk_1?run_id={run['id']}").json()
    assert detail["text"].startswith("Anisotropy")
    assert "source_path" not in detail["metadata"]
    assert client.get("/api/v1/documents?cursor=-1").status_code == 422
    assert client.get(f"/api/v1/runs/{run['id']}/artifacts/document").status_code == 404


def test_sse_resume_and_terminal_close(client):
    _, run = ready(client)
    registry = client.app.state.workbench.registry
    events = registry.events(run["id"])
    response = client.get(
        f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": str(events[0]["event_id"])}
    )
    assert response.status_code == 200
    assert f"id: {events[0]['event_id']}\n" not in response.text
    assert f"id: {events[-1]['event_id']}\n" in response.text
    assert "event: done" in response.text


def test_restart_requires_explicit_retry(client):
    doc = uploaded(client)
    run = queued(client, doc)
    service = client.app.state.workbench
    service.registry.event(run["id"], "upsert")
    service.registry.set_health(run["collection"], "writing", run["id"])
    service.registry.recover()
    assert service.registry.get("runs", run["id"])["status"] == "interrupted"
    assert service.registry.health(run["collection"])["state"] == "needs_repair"
    response = client.post(
        f"/api/v1/runs/{run['id']}/retry",
        json={"stage": "auto"},
        headers={"Idempotency-Key": "retry"},
    )
    assert response.status_code == 202
    assert response.json()["id"] != run["id"]


def test_needs_repair_blocks_retrieval(client):
    doc, run = ready(client)
    client.app.state.workbench.registry.set_health(run["collection"], "needs_repair", run["id"])
    response = client.post(
        "/api/v1/retrieval-runs", json={"query": "why"}, headers={"Idempotency-Key": "query"}
    )
    assert response.status_code == 409


def test_scope_and_top_k_validation(client):
    ready(client)
    assert (
        client.post(
            "/api/v1/retrieval-runs",
            json={"query": "why", "document_ids": ["missing"]},
            headers={"Idempotency-Key": "query"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/retrieval-runs",
            json={"query": "why", "top_k": 999},
            headers={"Idempotency-Key": "query"},
        ).status_code
        == 422
    )


def fake_query(monkeypatch):
    async def execute(*args, **kwargs):
        return SimpleNamespace(
            evidence_bundle={
                "evidence": [
                    {
                        "chunk_id": "chunk_1",
                        "text": "Anisotropy is induced by fluctuations.",
                        "scores": {"final": 3.0},
                    }
                ]
            },
            citations=[SimpleNamespace(chunk_id="chunk_1", index=1, score=3.0)],
        )

    monkeypatch.setattr(
        "src.mcp_server.tools.query_knowledge_hub.QueryKnowledgeHubTool.execute", execute
    )


def test_retrieval_only_does_not_generate_answer(client, monkeypatch):
    ready(client)
    fake_query(monkeypatch)

    def no_llm(*args, **kwargs):
        raise AssertionError("must not call answer LLM")

    monkeypatch.setattr("src.libs.llm.llm_factory.LLMFactory.create", no_llm)
    service = client.app.state.workbench
    run = service.create_retrieval(
        {"query": "why", "document_ids": [], "top_k": 5, "generate_answer": False}, "query"
    )
    service.execute(run["id"])
    assert service.registry.get("runs", run["id"])["status"] == "succeeded"
    result = service.retrieval_result(run["id"])
    assert result["answer_status"] == "not_requested"
    assert result["evidence"][0]["scores"]["final"] == 3.0
    assert result["evidence"][0]["run_id"]


def test_answer_separates_source_reference_numbers_from_evidence_ids(client, monkeypatch):
    ready(client)

    async def query(*a, **kw):
        return SimpleNamespace(
            evidence_bundle={
                "evidence": [
                    {
                        "chunk_id": "chunk_1",
                        "text": "Figure 3 uses the exponent from [28].",
                        "scores": {"final": 3.0},
                    }
                ]
            },
            citations=[SimpleNamespace(chunk_id="chunk_1", index=1, score=3.0)],
        )

    monkeypatch.setattr(
        "src.mcp_server.tools.query_knowledge_hub.QueryKnowledgeHubTool.execute", query
    )

    def chat(messages):
        assert "[28]" not in messages[1].content
        assert "原文参考编号 28" in messages[1].content
        assert "[1]" in messages[1].content
        return SimpleNamespace(content="图3使用原论文参考文献第28项的指数 [1]。", model="fixture")

    monkeypatch.setattr(
        "src.libs.llm.llm_factory.LLMFactory.create", lambda *a: SimpleNamespace(chat=chat)
    )
    service = client.app.state.workbench
    run = service.create_retrieval(
        {"query": "figure 3", "document_ids": [], "top_k": 5, "generate_answer": True},
        "reference-ids",
    )
    service.execute(run["id"])
    result = service.retrieval_result(run["id"])
    assert result["answer_status"] == "succeeded"
    assert "[28]" in result["evidence"][0]["text"]


def test_answer_failure_keeps_evidence_and_retry_skips_retrieval(client, monkeypatch):
    ready(client)
    fake_query(monkeypatch)
    monkeypatch.setattr(
        "src.libs.llm.llm_factory.LLMFactory.create",
        lambda *a: SimpleNamespace(chat=lambda *_: (_ for _ in ()).throw(RuntimeError("down"))),
    )
    service = client.app.state.workbench
    run = service.create_retrieval(
        {"query": "why", "document_ids": [], "top_k": 5, "generate_answer": True}, "answer"
    )
    service.execute(run["id"])
    assert service.registry.get("runs", run["id"])["status"] == "failed"
    assert service.retrieval_result(run["id"])["evidence"]
    retry = service.retry(run["id"], "answer", "answer-retry")
    monkeypatch.setattr(
        "src.mcp_server.tools.query_knowledge_hub.QueryKnowledgeHubTool.execute",
        lambda **k: (_ for _ in ()).throw(AssertionError("no retrieval")),
    )
    monkeypatch.setattr(
        "src.libs.llm.llm_factory.LLMFactory.create",
        lambda *a: SimpleNamespace(
            chat=lambda *_: SimpleNamespace(content="涨落诱导各向异性 [1]。", model="fixture")
        ),
    )
    service.execute(retry["id"])
    assert service.registry.get("runs", retry["id"])["status"] == "succeeded"
    assert service.retrieval_result(retry["id"])["answer_model"] == "fixture"


def test_cross_site_requests_rejected(client):
    response = client.get("/api/v1/config/public", headers={"Origin": "https://example.com"})
    assert response.status_code == 403


def test_failed_pipeline_result_never_displayed_as_success(client, monkeypatch):
    doc = uploaded(client)
    run = queued(client, doc)

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            self.loader = SimpleNamespace(load=lambda path: None)
            self.chunker = SimpleNamespace(split_document=lambda doc: [])
            self.dense_encoder = SimpleNamespace(
                embedding=SimpleNamespace(embed=lambda texts, **kw: [])
            )

        def run(self, path, trace, on_progress):
            on_progress("embed")
            return SimpleNamespace(
                success=False, error="failed", to_dict=lambda: {"success": False}
            )

        def close(self):
            pass

    monkeypatch.setattr("src.ingestion.pipeline.IngestionPipeline", FakePipeline)
    service = client.app.state.workbench
    service.execute(run["id"])
    assert service.registry.get("runs", run["id"])["status"] == "failed"
    assert service.registry.get("documents", doc["id"])["status"] == "failed"


def test_partial_storage_failure_blocks_reads_and_retry_reuses_vectors(client, monkeypatch):
    doc, old = ready(client)
    service = client.app.state.workbench
    service.registry.event(old["id"], "upsert", "failed", "fixture")
    run = service.retry(old["id"], "auto", "storage-test")
    attempts = []
    chunks = service.registry.chunks(old["id"])

    class Pipeline:
        def __init__(self, *a, **k):
            self.loader = SimpleNamespace(load=lambda p: None)
            self.chunker = SimpleNamespace(split_document=lambda d: [])

            def embed(texts, **kw):
                attempts.append("embedding")
                return [[0.0] * service.settings.embedding.dimensions for _ in texts]

            self.dense_encoder = SimpleNamespace(embedding=SimpleNamespace(embed=embed))

        def run(self, path, trace, on_progress):
            rid = trace.metadata["run_id"]
            service.registry.save_chunks(rid, [dict(c, run_id=rid) for c in chunks])
            on_progress("embed")
            self.dense_encoder.embedding.embed(["full text"], trace=trace)
            on_progress("upsert")
            if rid == run["id"]:
                raise RuntimeError("BM25 write failure")
            return SimpleNamespace(
                success=True,
                vector_ids=["v1"],
                stages={"storage": {"chroma": True, "bm25": True}},
                to_dict=lambda: {"success": True},
            )

        def close(self):
            pass

    monkeypatch.setattr("src.ingestion.pipeline.IngestionPipeline", Pipeline)
    service.execute(run["id"])
    assert service.registry.health(run["collection"])["state"] == "needs_repair"
    assert service.registry.get("runs", run["id"])["status"] == "failed"
    assert (service.folder(run["id"]) / "trace.json").exists()
    retry = service.retry(run["id"], "auto", "repair-test")
    service.execute(retry["id"])
    assert service.registry.get("runs", retry["id"])["status"] == "succeeded"
    assert service.registry.health(run["collection"])["state"] == "ready"
    assert attempts == ["embedding"]


def test_structured_history_import_is_idempotent_and_offline(client, tmp_path, monkeypatch):
    _, run = ready(client)
    service = client.app.state.workbench

    def forbidden(*a, **k):
        raise AssertionError("history import must be offline")

    monkeypatch.setattr("src.libs.llm.llm_factory.LLMFactory.create", forbidden)
    (tmp_path / "query-1.json").write_text(
        json.dumps(
            {
                "structuredContent": {
                    "metadata": {"query": "saved question"},
                    "citations": [{"chunk_id": "chunk_1", "index": 1, "score": 3.0}],
                    "evidenceBundle": {"evidence": []},
                }
            }
        ),
        encoding="utf-8",
    )
    service.import_queries(tmp_path, run)
    service.import_queries(tmp_path, run)
    queries = [r for r in service.registry.all("runs") if r["kind"] == "retrieval"]
    assert len(queries) == 1
    assert service.retrieval_result(queries[0]["id"])["evidence"][0]["chunk_id"] == "chunk_1"
