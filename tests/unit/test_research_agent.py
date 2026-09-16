"""Offline behavioral checks for the bounded research loop and citation gate."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agents.research import ResearchAgent, ResearchOptions, ResearchResult, render_draft
from src.core.response.citation_generator import Citation
from src.core.response.response_builder import MCPToolResponse


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.payloads = []

    def chat(self, messages, **kwargs):
        self.payloads.append(json.loads(messages[1].content))
        value = next(self.responses)
        return SimpleNamespace(content=value if isinstance(value, str) else json.dumps(value))


def evidence_response(*ids):
    return MCPToolResponse(
        "results",
        citations=[
            Citation(
                i,
                key,
                f"{key}.pdf",
                0.1,
                f"evidence for {key}",
                page=2,
                metadata={"title": key, "year": "2020"},
            )
            for i, key in enumerate(ids, 1)
        ],
        evidence_bundle={
            "evidence": [
                {
                    "chunk_id": key,
                    "text": f"evidence for {key}",
                    "document_id": key,
                    "title": key,
                    "page_start": 2,
                }
                for key in ids
            ]
        },
    )


def grade(sufficient=False, ids=None, queries=None):
    return {
        "sufficient": sufficient,
        "relevant_ids": ids or [],
        "gaps": [] if sufficient else ["Missing comparison"],
        "queries": queries or [],
    }


def draft(ids=None):
    return {
        "sections": [
            {
                "heading": "Methods",
                "claims": [{"text": "Supported comparison.", "evidence_ids": ids or ["E1"]}],
            }
        ]
    }


@pytest.mark.asyncio
async def test_rewrite_collects_unique_evidence_and_preserves_topic():
    retrieve = AsyncMock(side_effect=[evidence_response("a"), evidence_response("a", "b")])
    llm = ScriptedLLM(
        grade(ids=["E1"], queries=["RAG methods"]), grade(True, ["E1", "E2"]), draft(["E1", "E2"])
    )
    result = await ResearchAgent(llm, retrieve).run("RAG development")
    assert result.status == "draft"
    assert len(result.evidence) == 2
    assert [s["new_evidence"] for s in result.iterations] == [1, 1]
    assert retrieve.call_args_list[1].kwargs["query"] == "RAG methods"
    assert all(p["topic"] == "RAG development" for p in llm.payloads)
    assert "[E1] [E2]" in result.markdown


@pytest.mark.asyncio
async def test_empty_evidence_cannot_be_declared_sufficient():
    llm = ScriptedLLM(grade(True, ["E999"]))
    result = await ResearchAgent(llm, AsyncMock(return_value=evidence_response())).run("x")
    assert result.stop_reason == "no_evidence"
    assert result.status == "partial" and result.model_calls == 1


@pytest.mark.asyncio
async def test_invalid_json_does_not_fabricate_a_review():
    result = await ResearchAgent(
        ScriptedLLM("bad json"), AsyncMock(return_value=evidence_response("a"))
    ).run("x")
    assert result.status == "partial"
    assert any("assessment_failed" in w for w in result.warnings)
    assert result.model_calls == 1


@pytest.mark.asyncio
async def test_repeat_query_stops_without_extra_search():
    retrieve = AsyncMock(return_value=evidence_response("a"))
    llm = ScriptedLLM(grade(ids=["E1"], queries=["X", "x"]), draft())
    result = await ResearchAgent(llm, retrieve).run("x")
    assert retrieve.await_count == 1
    assert result.stop_reason == "no_new_queries"
    assert result.status == "partial"


@pytest.mark.asyncio
async def test_no_progress_stops_before_round_limit():
    retrieve = AsyncMock(return_value=evidence_response("a"))
    llm = ScriptedLLM(
        grade(ids=["E1"], queries=["second"]), grade(ids=["E1"], queries=["third"]), draft()
    )
    result = await ResearchAgent(llm, retrieve).run("first")
    assert len(result.iterations) == 2
    assert result.stop_reason == "no_new_evidence"


@pytest.mark.asyncio
async def test_round_limit_cannot_be_overridden_by_model():
    llm = ScriptedLLM(grade(ids=["E1"], queries=["one", "two", "three"]), draft())
    retrieve = AsyncMock(return_value=evidence_response("a"))
    result = await ResearchAgent(llm, retrieve, options=ResearchOptions(max_rounds=1)).run("x")
    assert retrieve.await_count == 1
    assert result.stop_reason == "round_limit"
    assert result.model_calls == 2


@pytest.mark.asyncio
async def test_retrieval_error_is_distinct_from_empty_results():
    response = MCPToolResponse("failed", metadata={"error": "private detail"})
    result = await ResearchAgent(ScriptedLLM(grade()), AsyncMock(return_value=response)).run("x")
    assert result.warnings == ["retrieval_failed:RuntimeError:round_1"]
    assert "private detail" not in json.dumps(result.to_dict())


@pytest.mark.asyncio
async def test_web_acquisition_requires_flag_and_reretrieves_same_query():
    acquire = AsyncMock(return_value=[{"rag_status": "indexed"}])
    retrieve = AsyncMock(side_effect=[evidence_response(), evidence_response("a", "b")])
    llm = ScriptedLLM(grade(queries=["x"]), grade(True, ["E1", "E2"]), draft())
    result = await ResearchAgent(
        llm, retrieve, acquire=acquire, options=ResearchOptions(allow_web=True)
    ).run("x")
    assert retrieve.await_count == 2 and acquire.await_count == 1
    assert result.acquisitions == [{"rag_status": "indexed"}]
    acquire.reset_mock()
    await ResearchAgent(
        ScriptedLLM(grade()), AsyncMock(return_value=evidence_response()), acquire=acquire
    ).run("x")
    acquire.assert_not_called()


def test_orphan_references_and_undated_events_are_removed():
    item = {
        "id": "E1",
        "year": "2020",
        "title": "known",
        "source": "known.pdf",
        "document_id": "a",
        "chunk_id": "a1",
        "page": 2,
    }
    result = ResearchResult("topic", "timeline", "default")
    md, count = render_draft(
        {
            "timeline": [
                {"year": "2020", "text": "Accepted", "evidence_ids": ["E1"]},
                {"year": "2021", "text": "Wrong year", "evidence_ids": ["E1"]},
                {"year": "2020", "text": "Invented source", "evidence_ids": ["E99"]},
                {"text": "No date", "evidence_ids": ["E1"]},
            ]
        },
        [item],
        result,
    )
    assert count == 1 and "Accepted" in md
    assert "Wrong year" not in md and "Invented source" not in md and "No date" not in md
    assert len(result.warnings) == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rounds": True},
        {"top_k": 0},
        {"max_papers": 6},
        {"allow_web": "yes"},
        {"call_timeout": float("nan")},
    ],
)
def test_invalid_budgets_rejected(kwargs):
    with pytest.raises(ValueError):
        ResearchOptions(**kwargs)


@pytest.mark.asyncio
async def test_invalid_collection_cannot_escape_download_directories():
    with pytest.raises(ValueError):
        await ResearchAgent(None, None).run("x", collection="../private")
