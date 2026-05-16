"""Table extraction utilities for paper-oriented ingestion.

This module provides a lightweight Markdown-table parser and persistence
logic for table content extracted from PDF-to-Markdown conversion.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class TableExtractor:
    """Extract and persist tables from Markdown text."""

    def __init__(self, table_storage_dir: str | Path = "data/tables") -> None:
        self.table_storage_dir = Path(table_storage_dir)

    def extract_tables_from_text(
        self,
        text: str,
        source_path: Optional[str] = None,
        max_tables: int = 50,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Extract Markdown tables and replace them with placeholders.

        Returns:
            (tables, modified_text)
        """
        if not text:
            return [], text

        lines = text.splitlines()
        tables: List[Dict[str, Any]] = []
        output_lines: List[str] = []

        i = 0
        table_index = 0
        while i < len(lines):
            if i + 1 < len(lines) and self._is_table_header(lines[i], lines[i + 1]):
                header_line = lines[i]
                sep_line = lines[i + 1]
                row_lines: List[str] = []
                i += 2
                while i < len(lines) and self._is_table_row(lines[i]):
                    row_lines.append(lines[i])
                    i += 1

                table_id = self._generate_table_id(source_path or "unknown", table_index)
                table_index += 1
                table = self._parse_markdown_table(
                    table_id, header_line, sep_line, row_lines, source_path
                )
                tables.append(table)
                output_lines.append(f"[TABLE: {table_id}]")

                if len(tables) >= max_tables:
                    output_lines.extend(lines[i:])
                    break
                continue

            output_lines.append(lines[i])
            i += 1

        return tables, "\n".join(output_lines)

    def save_tables(
        self,
        tables: List[Dict[str, Any]],
        collection: str = "default",
    ) -> List[str]:
        """Persist tables to data/tables/{collection}/{table_id}.json."""
        if not tables:
            return []

        table_dir = self.table_storage_dir / collection
        table_dir.mkdir(parents=True, exist_ok=True)

        paths: List[str] = []
        for table in tables:
            table_id = table.get("id") or "table"
            out_path = table_dir / f"{table_id}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(table, f, ensure_ascii=False, indent=2)
            paths.append(str(out_path))

        return paths

    @staticmethod
    def _generate_table_id(source_path: str, table_index: int) -> str:
        digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:8]
        return f"{digest}_table_{table_index:02d}"

    @staticmethod
    def _is_table_header(line: str, next_line: str) -> bool:
        return "|" in line and TableExtractor._is_separator_line(next_line)

    @staticmethod
    def _is_separator_line(line: str) -> bool:
        raw = line.strip()
        if not raw or "|" not in raw:
            return False
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        return all(set(p) <= {"-", ":"} for p in parts)

    @staticmethod
    def _is_table_row(line: str) -> bool:
        raw = line.strip()
        return raw != "" and "|" in raw

    @staticmethod
    def _split_row(line: str) -> List[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    def _parse_markdown_table(
        self,
        table_id: str,
        header_line: str,
        sep_line: str,
        row_lines: List[str],
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = self._split_row(header_line)
        rows = [self._split_row(row) for row in row_lines]
        csv_lines = [",".join(headers)] + [",".join(row) for row in rows]
        return {
            "id": table_id,
            "source_path": source_path,
            "headers": headers,
            "rows": rows,
            "csv": "\n".join(csv_lines),
        }
