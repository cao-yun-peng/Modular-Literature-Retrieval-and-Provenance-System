"""Research HTTP/worker contracts, using real agent logic and scripted providers."""

import json
import threading
from unittest.mock import AsyncMock

import pytest

from src.agents.research import ResearchAgent
from tests.unit.test_research_agent import ScriptedLLM, draft, evidence_response, grade
from tests.unit.test_web_api import client as client
from tests.unit.test_web_api import ready


def create(client, **changes):
    return client.post(
        "/api/v1/research-runs",
        json={"topic": "RAG methods", **changes},
        headers={"Idempotency-Key": "research"},
    )


def provider(monkeypatch, llm, retrieve):
    def build(settings, *, options, on_event):
        assert options.allow_web is False
        return ResearchAgent(llm, retrieve, options=options, on_event=on_event)

    monkeypatch.setattr("src.agents.runtime.build_agent", build)


def test_multiround_research_persists_live_snapshots_links_and_replay(client, monkeypatch):
    doc, ingestion = ready(client)
    service = client.app.state.workbench
    observed = []
    llm = ScriptedLLM(
        grade(ids=["E1"], queries=["comparison"]), grade(True, ["E1", "E2"]), draft(["E1", "E2"])
    )
    responses = iter([evidence_response("chunk_1"), evidence_response("chunk_1", "chunk_2")])

    async def retrieve(**kwargs):
        # Snapshot and started event are durable while the provider is still executing.
        observed.append((service.research_result(run["id"]), service.registry.events(run["id"])))
        return next(responses)

    provider(monkeypatch, llm, retrieve)
    response = create(client)
    assert response.status_code == 202
    run = response.json()
    assert create(client).json()["id"] == run["id"]
    assert create(client, topic="different").status_code == 409
    service.execute(run["id"])
    assert observed[0][0]["iterations"][0]["queries"] == ["RAG methods"]
    assert observed[0][1][-1]["phase"] == "started"
    assert observed[1][0]["evidence"][0]["id"] == "E1"
    result = client.get(f"/api/v1/research-runs/{run['id']}/result").json()
    assert result["status"] == "draft"
    assert "Missing comparison" in result["iterations"][0]["gaps"]
    assert result["iterations"][0]["next_queries"] == ["comparison"]
    assert result["evidence"][0]["document_id"] == doc["id"]
    assert result["evidence"][0]["run_id"] == ingestion["id"]
    assert result["evidence"][1]["document_id"] is None
    assert "source" not in result["evidence"][0]
    events = client.get(f"/api/v1/runs/{run['id']}/event-history").json()
    assert any(e["stage"] == "rewrite" and e["data"]["queries"] == ["comparison"] for e in events)
    assert all(e["status"] == "running" for e in events if e["phase"])
    assert events[-1]["status"] == "succeeded"
    after = events[-3]["event_id"]
    stream = client.get(f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": str(after)})
    assert f"id: {after}\n" not in stream.text
    assert f"id: {events[-1]['event_id']}\n" in stream.text and "event: done" in stream.text
    assert client.get("/api/v1/runs?kind=research").json()["items"][0]["id"] == run["id"]
    report = client.get(f"/api/v1/runs/{run['id']}/artifacts/research_report")
    assert "[E1] [E2]" in report.text and "attachment" in report.headers["content-disposition"]
    saved = client.get(f"/api/v1/runs/{run['id']}/artifacts/research_data").json()
    assert saved == result


@pytest.mark.parametrize(
    "change",
    [
        {"topic": " "},
        {"max_rounds": 6},
        {"max_rounds": True},
        {"queries_per_round": 4},
        {"top_k": 20},
        {"mode": "unknown"},
        {"allow_web": True},
        {"zotero_target": "L1"},
    ],
)
def test_research_rejects_invalid_options_and_external_permissions(client, change):
    assert create(client, **change).status_code == 422


def test_research_empty_and_unhealthy_collection(client):
    assert create(client).json()["error"]["code"] == "empty_collection"
    ready(client)
    service = client.app.state.workbench
    service.registry.set_health(service.collection(None), "needs_repair")
    assert create(client).json()["error"]["code"] == "collection_unavailable"


def test_insufficient_evidence_is_not_a_successful_draft(client, monkeypatch):
    ready(client)
    provider(monkeypatch, ScriptedLLM(grade()), AsyncMock(return_value=evidence_response()))
    run = create(client).json()
    service = client.app.state.workbench
    service.execute(run["id"])
    assert service.registry.get("runs", run["id"])["status"] == "succeeded"
    result = service.research_result(run["id"])
    assert result["status"] == "insufficient" and result["stop_reason"] == "no_evidence"
    assert not result["evidence"]


def test_model_failure_preserves_evidence_and_retries_as_new_research(client, monkeypatch):
    ready(client)
    provider(
        monkeypatch,
        ScriptedLLM(grade(True, ["E1"]), "invalid json"),
        AsyncMock(return_value=evidence_response("chunk_1")),
    )
    run = create(client, mode="answer").json()
    service = client.app.state.workbench
    service.execute(run["id"])
    assert service.registry.get("runs", run["id"])["status"] == "failed"
    assert service.research_result(run["id"])["evidence"][0]["id"] == "E1"
    assert any(
        e.get("phase") == "failed" and e["status"] == "running"
        for e in service.registry.events(run["id"])
    )
    retry = client.post(
        f"/api/v1/runs/{run['id']}/retry",
        json={"stage": "auto"},
        headers={"Idempotency-Key": "retry"},
    ).json()
    assert retry["id"] != run["id"] and retry["kind"] == "research"
    assert retry["retry_of"] == run["id"] and retry["status"] == "queued"
    assert service.research_result(retry["id"])["evidence"] == []
    invalid = client.post(
        f"/api/v1/runs/{run['id']}/retry",
        json={"stage": "answer"},
        headers={"Idempotency-Key": "answer-retry"},
    )
    assert invalid.status_code == 422


def test_research_restart_preserves_snapshot_and_requires_retry(client, monkeypatch):
    ready(client)
    run = create(client).json()
    service = client.app.state.workbench
    service.registry.event(run["id"], "assessment")
    snapshot = service.research_result(run["id"])
    snapshot["gaps"] = ["Saved before restart"]
    service.write(service.folder(run["id"]), "research.json", snapshot)
    service.registry.recover()
    assert service.registry.get("runs", run["id"])["status"] == "interrupted"
    assert service.research_result(run["id"])["gaps"] == ["Saved before restart"]
    assert service.research_result(run["id"])["status"] == "failed"


def test_assessment_failure_is_not_mislabeled_as_no_evidence(client, monkeypatch):
    ready(client)
    provider(
        monkeypatch, ScriptedLLM("not JSON"), AsyncMock(return_value=evidence_response("chunk_1"))
    )
    run = create(client).json()
    service = client.app.state.workbench
    service.execute(run["id"])
    result = service.research_result(run["id"])
    assert result["stop_reason"] == "assessment_failed" and result["status"] == "failed"
    assert "not JSON" not in json.dumps(result)


def test_result_endpoint_rejects_other_run_types(client):
    _, run = ready(client)
    assert client.get(f"/api/v1/research-runs/{run['id']}/result").status_code == 422


def test_frozen_baseline_allows_local_research_without_changing_documents(client, monkeypatch):
    from src.core.baseline_guard import baseline_marker

    ready(client)
    service = client.app.state.workbench
    before = service.registry.all("documents")
    marker = baseline_marker(service.settings, service.collection(None))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"status":"frozen"}')
    provider(
        monkeypatch,
        ScriptedLLM(grade(True, ["E1"]), draft()),
        AsyncMock(return_value=evidence_response("chunk_1")),
    )
    run = create(client, mode="answer").json()
    service.execute(run["id"])
    assert service.research_result(run["id"])["status"] == "draft"
    assert service.registry.all("documents") == before
    assert marker.read_text() == '{"status":"frozen"}'


def test_research_dispatcher_executes_only_one_task_at_a_time(client, monkeypatch):
    ready(client)
    service = client.app.state.workbench
    first_started, release, second_started = (threading.Event() for _ in range(3))
    calls = []

    def execute(run_id):
        calls.append(run_id)
        service.registry.event(run_id, "starting")
        if len(calls) == 1:
            first_started.set()
            assert release.wait(5)
        else:
            second_started.set()
        service.registry.event(run_id, "completed", "succeeded")

    monkeypatch.setattr(service, "execute", execute)
    service.start()
    try:
        first = create(client).json()
        assert first_started.wait(3)
        second = client.post(
            "/api/v1/research-runs",
            json={"topic": "second task"},
            headers={"Idempotency-Key": "second"},
        ).json()
        assert not second_started.wait(0.8)
        assert service.registry.get("runs", second["id"])["status"] == "queued"
        release.set()
        assert second_started.wait(3)
        assert calls == [first["id"], second["id"]]
    finally:
        release.set()
        service.close()
