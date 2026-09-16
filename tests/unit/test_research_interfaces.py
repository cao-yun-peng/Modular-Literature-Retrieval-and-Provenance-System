"""MCP registration, response and CLI option contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts.research import parse_args
from src.agents.research import ResearchResult
from src.mcp_server.tools.research_topic import register_tool, research_topic_handler


def test_registration():
    handler = Mock()
    register_tool(handler)
    assert handler.register_tool.call_args.kwargs["name"] == "research_topic"
    assert "allow_web" not in handler.register_tool.call_args.kwargs["input_schema"]["properties"]


@pytest.mark.asyncio
async def test_mcp_returns_structured_trace(monkeypatch):
    result = ResearchResult("topic", "review", "default", markdown="draft")
    monkeypatch.setattr("src.core.settings.load_settings", lambda: object())
    monkeypatch.setattr(
        "src.agents.runtime.build_agent",
        lambda *a, **k: SimpleNamespace(run=AsyncMock(return_value=result)),
    )
    response = await research_topic_handler("topic")
    assert response.structuredContent["topic"] == "topic"
    assert response.content[0].text == "draft"


@pytest.mark.parametrize(
    "args",
    [
        ["--topic", "x", "--zotero-target", "L1"],
        ["--topic", "x", "--allow-web", "--max-rounds", "1"],
    ],
)
def test_cli_rejects_inconsistent_permissions(args):
    with pytest.raises(SystemExit):
        parse_args(args)


def test_cli_defaults_to_local_only():
    args = parse_args(["--topic", "RAG"])
    assert args.allow_web is False and args.zotero_target is None
