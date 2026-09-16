"""Real MCP stdio round trip with a deterministic evidence tool (no live models)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_evidence_bundle_stdio_preserves_locator_and_handoff(tmp_path):
    child = tmp_path / "evidence_server.py"
    root = Path(__file__).resolve().parents[2]
    child.write_text(
        """import asyncio
from mcp.server.stdio import stdio_server
from mcp import types
from src.mcp_server.protocol_handler import ProtocolHandler, create_mcp_server
from src.core.response.response_builder import MCPToolResponse
from src.core.response.evidence_bundle import EvidenceBundleBuilder
from src.core.query_engine.fulltext_handoff_policy import FulltextHandoffPolicy
from src.core.settings import AgentHandoffSettings
from src.core.types import RetrievalResult

async def query(query):
    found = [RetrievalResult(chunk_id='c1', score=.8, text='Verified fixture evidence.',
        metadata={'document_id':'p1','page_start':2,'page_end':2,'source_path':'fixture.pdf'})]
    decision = FulltextHandoffPolicy(AgentHandoffSettings(enabled=True)).decide(query, found)
    bundle = EvidenceBundleBuilder().build(query=query, collection='fixture', requested_mode='evidence',
        selected_mode='evidence', results=found, citations=[], decision=decision)
    response = MCPToolResponse(content='Evidence [1]', metadata={'fixture':True}, evidence_bundle=bundle)
    return types.CallToolResult(content=response.to_mcp_content(),
        structuredContent=response.to_dict()['structuredContent'],isError=False)

async def main():
    handler=ProtocolHandler('benchmark-fixture','1')
    handler.register_tool('query_knowledge_hub','fixture',{'type':'object','properties':{'query':{'type':'string'}},'required':['query']},query)
    server=create_mcp_server('benchmark-fixture','1',handler,register_tools=False)
    async with stdio_server() as (reader,writer):
        await server.run(reader,writer,server.create_initialization_options())
asyncio.run(main())
""",
        encoding="utf-8",
    )

    async def check():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"}
        async with stdio_client(
            StdioServerParameters(command=sys.executable, args=[str(child)], env=env)
        ) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result = await session.call_tool(
                    "query_knowledge_hub", {"query": "Summarize the entire paper"}
                )
                assert not result.isError
                bundle = result.structuredContent["evidenceBundle"]
                assert bundle["evidence"][0]["page_start"] == 2
                assert bundle["evidence"][0]["document_id"] == "p1"
                assert bundle["recommended_next_action"] is None  # No verified Zotero attachment.
                text = next(
                    b.text for b in result.content if '"evidenceBundle"' in getattr(b, "text", "")
                )
                block = json.loads(text[text.index("{") : text.rindex("}") + 1])
                assert block["evidenceBundle"] == bundle
                missing = await session.call_tool("query_knowledge_hub", {})
                assert missing.isError

    asyncio.run(asyncio.wait_for(check(), timeout=30))
