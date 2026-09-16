"""Read-only local research Agent exposed via MCP."""

from __future__ import annotations

from mcp import types

from src.agents.research import ResearchOptions

TOOL_NAME = "research_topic"
TOOL_DESCRIPTION = (
    "Run bounded local RAG research with query rewriting and evidence assessment. "
    "Return a cited review draft, timeline, or answer plus evidence, gaps and loop trace. "
    "Requires the configured LLM. Does not download papers or write to Zotero; "
    "use scripts/research.py with explicit acquisition options for that workflow."
)
TOOL_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topic": {"type": "string", "minLength": 1, "maxLength": 2000},
        "mode": {"type": "string", "enum": ["review", "timeline", "answer"], "default": "review"},
        "collection": {"type": "string", "default": "default", "pattern": r"^[\w-]{1,80}$"},
        "max_rounds": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    },
    "required": ["topic"],
}


async def research_topic_handler(topic, mode="review", collection="default", max_rounds=3, top_k=5):
    from src.agents.runtime import build_agent
    from src.core.settings import load_settings

    try:
        options = ResearchOptions(max_rounds=max_rounds, top_k=top_k)
        agent = build_agent(load_settings(), options=options)
        result = await agent.run(topic, mode=mode, collection=collection)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.markdown)],
            structuredContent=result.to_dict(),
            isError=False,
        )
    except ValueError:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text="Invalid research parameters or model configuration."
                )
            ],
            isError=True,
        )
    except Exception:
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text="Research initialization failed; check model and retrieval configuration.",
                )
            ],
            isError=True,
        )


def register_tool(protocol_handler):
    protocol_handler.register_tool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=research_topic_handler,
    )
