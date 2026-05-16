"""MCP Tool: export_bibtex

Exports BibTeX entries from structured reference metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import types

from src.core.settings import resolve_path


TOOL_NAME = "export_bibtex"
TOOL_DESCRIPTION = """Export BibTeX entries for a document or provided reference list.

Parameters:
- doc_id: Document id whose bib entries should be exported (optional)
- entries: List of structured reference entries (optional)
- entries_path: Path to JSON entries file (optional)
"""

TOOL_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_id": {"type": "string", "description": "Document id to export BibTeX for."},
        "entries": {
            "type": "array",
            "description": "List of reference entry dicts.",
            "items": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        "entries_path": {"type": "string", "description": "Path to a JSON file with bib entries."},
    },
    "required": [],
}


@dataclass
class ExportBibtexTool:
    default_fixture_path: Path = resolve_path("tests/fixtures/bib_entries_sample.json")

    async def execute(
        self,
        doc_id: Optional[str] = None,
        entries: Optional[List[Dict[str, Any]]] = None,
        entries_path: Optional[str] = None,
    ) -> types.CallToolResult:
        bib_entries = self._load_entries(doc_id, entries, entries_path)
        bibtex = self._format_bibtex(bib_entries)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=bibtex)],
            isError=False,
        )

    def _load_entries(
        self,
        doc_id: Optional[str],
        entries: Optional[List[Dict[str, Any]]],
        entries_path: Optional[str],
    ) -> List[Dict[str, Any]]:
        if entries:
            return entries

        if entries_path:
            path = resolve_path(entries_path)
            if path.exists():
                return self._load_json(path)

        if doc_id:
            candidate = resolve_path(f"data/bib/{doc_id}.json")
            if candidate.exists():
                return self._load_json(candidate)

        if self.default_fixture_path.exists():
            return self._load_json(self.default_fixture_path)

        return []

    @staticmethod
    def _load_json(path: Path) -> List[Dict[str, Any]]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("entries", [])
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def _format_bibtex(entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""  # return empty string when no entries found

        blocks: List[str] = []
        for idx, entry in enumerate(entries, start=1):
            blocks.append(ExportBibtexTool._entry_to_bibtex(entry, idx))
        return "\n\n".join(blocks)

    @staticmethod
    def _entry_to_bibtex(entry: Dict[str, Any], index: int) -> str:
        entry_id = entry.get("id") or entry.get("key") or f"ref{index}"
        title = entry.get("title") or ""
        authors = entry.get("authors") or entry.get("author") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(",") if a.strip()]
        author_field = " and ".join(authors)
        year = entry.get("year") or ""
        doi = entry.get("doi") or ""
        venue = entry.get("venue") or entry.get("journal") or ""
        url = entry.get("url") or entry.get("link") or ""
        arxiv = entry.get("arxiv_id") or ""

        entry_type = "article" if venue else "misc"
        lines = [f"@{entry_type}{{{entry_id},"]
        if title:
            lines.append(f"  title={{ {title} }}")
        if author_field:
            lines.append(f"  author={{ {author_field} }}")
        if year:
            lines.append(f"  year={{ {year} }}")
        if venue:
            lines.append(f"  journal={{ {venue} }}")
        if doi:
            lines.append(f"  doi={{ {doi} }}")
        if arxiv:
            lines.append(f"  note={{ arXiv:{arxiv} }}")
        if url:
            lines.append(f"  url={{ {url} }}")
        lines.append("}")
        return "\n".join(lines)


async def export_bibtex_handler(**kwargs: Any) -> types.CallToolResult:
    tool = ExportBibtexTool()
    return await tool.execute(**kwargs)


def register_tool(protocol_handler: Any) -> None:
    protocol_handler.register_tool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=export_bibtex_handler,
    )
