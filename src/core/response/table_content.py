"""Table content structures for MCP responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mcp import types


@dataclass
class TableContent:
    table_id: str
    headers: List[str]
    rows: List[List[str]]
    csv: Optional[str] = None
    source_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_id": self.table_id,
            "headers": self.headers,
            "rows": self.rows,
            "csv": self.csv,
            "source_path": self.source_path,
        }

    def to_mcp_content(self) -> types.TextContent:
        """Represent table content as a JSON text block."""
        import json

        return types.TextContent(
            type="text",
            text=f"\n---\n**Table:**\n```json\n{json.dumps(self.to_dict(), ensure_ascii=False, indent=2)}\n```",
        )
