"""
MCP Server Tools.

This package contains the MCP tool definitions exposed to clients.
"""

from src.mcp_server.tools.query_knowledge_hub import (
    TOOL_NAME as QUERY_KNOWLEDGE_HUB_NAME,
    TOOL_DESCRIPTION as QUERY_KNOWLEDGE_HUB_DESCRIPTION,
    TOOL_INPUT_SCHEMA as QUERY_KNOWLEDGE_HUB_SCHEMA,
    QueryKnowledgeHubTool,
    query_knowledge_hub_handler,
    register_tool as register_query_knowledge_hub,
)
from src.mcp_server.tools.export_bibtex import (
    TOOL_NAME as EXPORT_BIBTEX_NAME,
    TOOL_DESCRIPTION as EXPORT_BIBTEX_DESCRIPTION,
    TOOL_INPUT_SCHEMA as EXPORT_BIBTEX_SCHEMA,
    export_bibtex_handler,
    register_tool as register_export_bibtex,
)

__all__ = [
    "QUERY_KNOWLEDGE_HUB_NAME",
    "QUERY_KNOWLEDGE_HUB_DESCRIPTION",
    "QUERY_KNOWLEDGE_HUB_SCHEMA",
    "QueryKnowledgeHubTool",
    "query_knowledge_hub_handler",
    "register_query_knowledge_hub",
    "EXPORT_BIBTEX_NAME",
    "EXPORT_BIBTEX_DESCRIPTION",
    "EXPORT_BIBTEX_SCHEMA",
    "export_bibtex_handler",
    "register_export_bibtex",
]
