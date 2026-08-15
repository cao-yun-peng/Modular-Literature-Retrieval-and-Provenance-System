"""SQLite store for Parent–Child mappings and derived section context."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.types import Chunk
from src.ingestion.chunking.hierarchical_chunker import SectionParent


@dataclass(frozen=True)
class StoredSection:
    parent_id: str
    document_id: str
    text: str
    section_path: tuple[str, ...]
    child_ids: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    metadata: dict[str, Any]


class SectionStore:
    """Persist derived hierarchy separately from dense and sparse indexes."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sections (
                    collection TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    section_path_json TEXT NOT NULL,
                    child_ids_json TEXT NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    metadata_json TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    PRIMARY KEY (collection, parent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sections_document
                ON sections(collection, document_id);
                CREATE TABLE IF NOT EXISTS section_children (
                    collection TEXT NOT NULL,
                    child_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    previous_chunk_id TEXT,
                    next_chunk_id TEXT,
                    PRIMARY KEY (collection, child_id)
                );
                CREATE INDEX IF NOT EXISTS idx_section_children_parent
                ON section_children(collection, parent_id);
                """
            )

    def upsert(
        self,
        collection: str,
        parents: list[SectionParent],
        children: list[Chunk],
        schema_version: str,
    ) -> None:
        """Atomically persist section records and child adjacency."""
        with self._connect() as connection:
            for parent in parents:
                connection.execute(
                    """
                    INSERT INTO sections (
                        collection, parent_id, document_id, text,
                        section_path_json, child_ids_json, page_start, page_end,
                        metadata_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection, parent_id) DO UPDATE SET
                        document_id=excluded.document_id,
                        text=excluded.text,
                        section_path_json=excluded.section_path_json,
                        child_ids_json=excluded.child_ids_json,
                        page_start=excluded.page_start,
                        page_end=excluded.page_end,
                        metadata_json=excluded.metadata_json,
                        schema_version=excluded.schema_version
                    """,
                    (
                        collection,
                        parent.parent_id,
                        parent.document_id,
                        parent.text,
                        json.dumps(parent.section_path, ensure_ascii=False),
                        json.dumps(parent.child_ids, ensure_ascii=False),
                        parent.page_start,
                        parent.page_end,
                        json.dumps(parent.metadata, ensure_ascii=False),
                        schema_version,
                    ),
                )
            for child in children:
                metadata = child.metadata
                connection.execute(
                    """
                    INSERT INTO section_children (
                        collection, child_id, parent_id, document_id,
                        previous_chunk_id, next_chunk_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection, child_id) DO UPDATE SET
                        parent_id=excluded.parent_id,
                        document_id=excluded.document_id,
                        previous_chunk_id=excluded.previous_chunk_id,
                        next_chunk_id=excluded.next_chunk_id
                    """,
                    (
                        collection,
                        child.id,
                        metadata.get("parent_id", ""),
                        metadata.get("document_id", metadata.get("source_ref", "")),
                        metadata.get("previous_chunk_id") or None,
                        metadata.get("next_chunk_id") or None,
                    ),
                )

    def get_parent_for_child(self, collection: str, child_id: str) -> StoredSection | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.* FROM sections s
                JOIN section_children c
                  ON c.collection = s.collection AND c.parent_id = s.parent_id
                WHERE c.collection = ? AND c.child_id = ?
                """,
                (collection, child_id),
            ).fetchone()
        return self._stored(row) if row else None

    def get_neighbor_ids(self, collection: str, child_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT previous_chunk_id, next_chunk_id FROM section_children
                WHERE collection = ? AND child_id = ?
                """,
                (collection, child_id),
            ).fetchone()
        if row is None:
            return ()
        return tuple(
            value
            for value in (row["previous_chunk_id"], row["next_chunk_id"])
            if value
        )

    def delete_document(self, collection: str, document_id: str) -> int:
        """Delete only derived hierarchy for one replaced project document."""
        with self._connect() as connection:
            child_count = connection.execute(
                "SELECT COUNT(*) FROM section_children WHERE collection=? AND document_id=?",
                (collection, document_id),
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM section_children WHERE collection=? AND document_id=?",
                (collection, document_id),
            )
            connection.execute(
                "DELETE FROM sections WHERE collection=? AND document_id=?",
                (collection, document_id),
            )
        return int(child_count)

    def update_document_metadata(
        self,
        collection: str,
        document_id: str,
        metadata_updates: dict[str, Any],
    ) -> int:
        """Update the source snapshot for derived parents only."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT parent_id, metadata_json FROM sections WHERE collection=? AND document_id=?",
                (collection, document_id),
            ).fetchall()
            for row in rows:
                metadata = json.loads(row["metadata_json"])
                metadata.update(metadata_updates)
                connection.execute(
                    "UPDATE sections SET metadata_json=? WHERE collection=? AND parent_id=?",
                    (
                        json.dumps(metadata, ensure_ascii=False),
                        collection,
                        row["parent_id"],
                    ),
                )
        return len(rows)

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredSection:
        return StoredSection(
            parent_id=row["parent_id"],
            document_id=row["document_id"],
            text=row["text"],
            section_path=tuple(json.loads(row["section_path_json"])),
            child_ids=tuple(json.loads(row["child_ids_json"])),
            page_start=row["page_start"],
            page_end=row["page_end"],
            metadata=json.loads(row["metadata_json"]),
        )
