"""Compatibility entry point for the Modular RAG MCP Server."""

import sys

from src.mcp_server.server import run_stdio_server


def main() -> int:
    """Start the real stdio MCP server.

    Keeping this wrapper preserves ``python main.py`` compatibility while the
    installed console script points directly at the canonical server module.
    """
    return run_stdio_server()


if __name__ == "__main__":
    sys.exit(main())
