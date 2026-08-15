"""Planning and execution of idempotent Zotero source synchronization."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.integrations.zotero.models import SourceDocument
from src.integrations.zotero.state import ZoteroSyncState, ZoteroSyncStateStore


class SyncAction(str, Enum):
    """One explicit action selected after comparing source and local state."""

    ADD = "add"
    UPDATE = "update"
    SKIP = "skip"


@dataclass(frozen=True)
class SyncPlanEntry:
    """A planned action for one Zotero PDF attachment."""

    document: SourceDocument
    target_collection: str
    file_sha256: str
    action: SyncAction
    reason: str


@dataclass
class SyncRunResult:
    """Machine-readable summary of a sync run."""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    inactive: int = 0
    errors: int = 0
    entries: list[dict[str, str]] = field(default_factory=list)


IngestCallback = Callable[[SourceDocument], tuple[bool, str | None, str | None]]


class ZoteroSyncService:
    """Compare Zotero PDF identities with a collection-scoped SQLite state store."""

    def __init__(self, state_store: ZoteroSyncStateStore) -> None:
        self.state_store = state_store

    def plan(
        self, documents: Iterable[SourceDocument], target_collection: str
    ) -> list[SyncPlanEntry]:
        """Plan adds, updates and skips without mutating state or indexes."""
        if not target_collection.strip():
            raise ValueError("target_collection cannot be empty")
        entries: list[SyncPlanEntry] = []
        for document in documents:
            file_sha256 = self.compute_sha256(document.attachment.local_path)
            current = self.state_store.get(
                document.item_key, document.attachment_key, target_collection
            )
            if (
                current
                and current.status == "synced"
                and current.file_sha256 == file_sha256
                and current.source_version == self._source_version(document)
            ):
                action = SyncAction.SKIP
                reason = "unchanged_source_version_and_sha256"
            elif current is None:
                action = SyncAction.ADD
                reason = "new_attachment"
            else:
                action = SyncAction.UPDATE
                reason = "source_version_or_sha256_changed"
            entries.append(
                SyncPlanEntry(
                    document=document,
                    target_collection=target_collection,
                    file_sha256=file_sha256,
                    action=action,
                    reason=reason,
                )
            )
        return entries

    def execute(
        self,
        plan: Iterable[SyncPlanEntry],
        ingest: IngestCallback,
        *,
        target_collection: str | None = None,
        mark_missing_inactive: bool = True,
    ) -> SyncRunResult:
        """Execute a plan, preserving successful state for unaffected attachments."""
        entries = list(plan)
        result = SyncRunResult()
        active_keys = {
            (entry.document.item_key, entry.document.attachment_key) for entry in entries
        }
        collections = {entry.target_collection for entry in entries}
        if len(collections) > 1:
            raise ValueError("A sync execution must target exactly one collection")
        inferred_collection = next(iter(collections), "")
        if target_collection is not None and target_collection.strip() == "":
            raise ValueError("target_collection cannot be empty")
        if target_collection and inferred_collection and target_collection != inferred_collection:
            raise ValueError("target_collection does not match the sync plan")
        effective_collection = target_collection or inferred_collection

        for entry in entries:
            if entry.action == SyncAction.SKIP:
                result.skipped += 1
                result.entries.append(self._entry_result(entry, "skipped"))
                continue
            try:
                success, document_id, error = ingest(entry.document)
            except Exception as exc:  # callback boundary: persist a useful sync error
                success, document_id, error = False, None, str(exc)
            if success:
                self.state_store.save(
                    ZoteroSyncState(
                        item_key=entry.document.item_key,
                        attachment_key=entry.document.attachment_key,
                        target_collection=entry.target_collection,
                        source_version=self._source_version(entry.document),
                        file_sha256=entry.file_sha256,
                        document_id=document_id,
                        status="synced",
                        last_synced_at=self.state_store.now(),
                    )
                )
                if entry.action == SyncAction.ADD:
                    result.added += 1
                else:
                    result.updated += 1
                result.entries.append(self._entry_result(entry, "synced"))
            else:
                result.errors += 1
                self.state_store.save(
                    ZoteroSyncState(
                        item_key=entry.document.item_key,
                        attachment_key=entry.document.attachment_key,
                        target_collection=entry.target_collection,
                        source_version=self._source_version(entry.document),
                        file_sha256=entry.file_sha256,
                        document_id=None,
                        status="error",
                        last_synced_at=self.state_store.now(),
                        error_type="ingestion_error",
                        error_message=error or "unknown ingestion error",
                    )
                )
                result.entries.append(self._entry_result(entry, "error", error or "unknown ingestion error"))

        if mark_missing_inactive and effective_collection:
            result.inactive = self.state_store.mark_inactive_not_in(
                effective_collection, active_keys
            )
        return result

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _source_version(document: SourceDocument) -> str:
        return f"{document.item_version}:{document.attachment.version}"

    @staticmethod
    def _entry_result(
        entry: SyncPlanEntry, status: str, error: str | None = None
    ) -> dict[str, str]:
        payload = {
            "item_key": entry.document.item_key,
            "attachment_key": entry.document.attachment_key,
            "action": entry.action.value,
            "status": status,
            "reason": entry.reason,
        }
        if error:
            payload["error"] = error
        return payload
