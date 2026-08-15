"""SQLite state store for idempotent Zotero-to-index synchronization."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ZoteroSyncState:
    """Last known state for one attachment in one project collection."""

    item_key: str
    attachment_key: str
    target_collection: str
    source_version: str
    file_sha256: str
    document_id: str | None
    status: str
    last_synced_at: str
    error_type: str | None = None
    error_message: str | None = None


class ZoteroSyncStateStore:
    """Persist source identities independently from the legacy file hash table."""

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS zotero_sync_state (
                    item_key TEXT NOT NULL,
                    attachment_key TEXT NOT NULL,
                    target_collection TEXT NOT NULL,
                    source_version TEXT NOT NULL DEFAULT '',
                    file_sha256 TEXT NOT NULL DEFAULT '',
                    document_id TEXT,
                    status TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    PRIMARY KEY (item_key, attachment_key, target_collection)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_zotero_sync_collection_status
                ON zotero_sync_state(target_collection, status)
                """
            )

    def get(
        self, item_key: str, attachment_key: str, target_collection: str
    ) -> ZoteroSyncState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM zotero_sync_state
                WHERE item_key = ? AND attachment_key = ? AND target_collection = ?
                """,
                (item_key, attachment_key, target_collection),
            ).fetchone()
        return self._to_state(row) if row else None

    def save(self, state: ZoteroSyncState) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO zotero_sync_state (
                    item_key, attachment_key, target_collection, source_version,
                    file_sha256, document_id, status, last_synced_at,
                    error_type, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key, attachment_key, target_collection) DO UPDATE SET
                    source_version = excluded.source_version,
                    file_sha256 = excluded.file_sha256,
                    document_id = excluded.document_id,
                    status = excluded.status,
                    last_synced_at = excluded.last_synced_at,
                    error_type = excluded.error_type,
                    error_message = excluded.error_message
                """,
                (
                    state.item_key,
                    state.attachment_key,
                    state.target_collection,
                    state.source_version,
                    state.file_sha256,
                    state.document_id,
                    state.status,
                    state.last_synced_at,
                    state.error_type,
                    state.error_message,
                ),
            )

    def mark_inactive_not_in(
        self, target_collection: str, active_attachment_keys: set[tuple[str, str]]
    ) -> int:
        """Mark vanished source attachments inactive without deleting index data."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_key, attachment_key FROM zotero_sync_state
                WHERE target_collection = ? AND status != 'inactive'
                """,
                (target_collection,),
            ).fetchall()
            stale = [
                (row["item_key"], row["attachment_key"])
                for row in rows
                if (row["item_key"], row["attachment_key"])
                not in active_attachment_keys
            ]
            now = self.now()
            for item_key, attachment_key in stale:
                connection.execute(
                    """
                    UPDATE zotero_sync_state
                    SET status = 'inactive', last_synced_at = ?,
                        error_type = NULL, error_message = NULL
                    WHERE item_key = ? AND attachment_key = ? AND target_collection = ?
                    """,
                    (now, item_key, attachment_key, target_collection),
                )
        return len(stale)

    def collection_summary(self, target_collection: str) -> dict[str, object]:
        """Return aggregate sync health without exposing local attachment paths."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count, MAX(last_synced_at) AS latest
                FROM zotero_sync_state
                WHERE target_collection = ?
                GROUP BY status
                """,
                (target_collection,),
            ).fetchall()
        status_counts = {str(row["status"]): int(row["count"]) for row in rows}
        latest = max(
            (str(row["latest"]) for row in rows if row["latest"]),
            default=None,
        )
        return {
            "active": status_counts.get("synced", 0),
            "inactive": status_counts.get("inactive", 0),
            "errors": status_counts.get("error", 0),
            "last_synced_at": latest,
        }

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_state(row: sqlite3.Row) -> ZoteroSyncState:
        return ZoteroSyncState(
            item_key=row["item_key"],
            attachment_key=row["attachment_key"],
            target_collection=row["target_collection"],
            source_version=row["source_version"],
            file_sha256=row["file_sha256"],
            document_id=row["document_id"],
            status=row["status"],
            last_synced_at=row["last_synced_at"],
            error_type=row["error_type"],
            error_message=row["error_message"],
        )
