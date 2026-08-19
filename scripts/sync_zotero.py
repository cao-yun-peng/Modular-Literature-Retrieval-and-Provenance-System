#!/usr/bin/env python
"""Synchronize Zotero PDF attachments into a project collection (read-only).

The command reads Zotero Desktop's local API and never writes to the Zotero
library.  It uses a separate, collection-scoped SQLite state table to decide
whether an attachment needs ingestion.  Existing vector records are never
deleted by this command; removed Zotero attachments are only marked inactive
in its sync state so a later cleanup can be reviewed explicitly.

Examples:
    python scripts/sync_zotero.py --collection-key ABCD1234 --target-collection papers --dry-run
    python scripts/sync_zotero.py --collection-key ABCD1234 --target-collection papers --paper-loader
"""

# ruff: noqa: E402 -- the project root must be added before local imports

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.settings import load_settings, resolve_path
from src.core.trace import TraceCollector, TraceContext
from src.ingestion.pipeline import IngestionPipeline
from src.integrations.zotero.client import ZoteroClientError, ZoteroLocalClient
from src.integrations.zotero.models import SourceDocument
from src.integrations.zotero.state import ZoteroSyncStateStore
from src.integrations.zotero.sync_service import SyncAction, SyncRunResult, ZoteroSyncService
from src.observability.logger import get_logger

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse explicit source and project collection scope for one sync run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collection-key",
        "--zotero-collection",
        dest="zotero_collection",
        required=True,
        help="Zotero collection key to read through the local read-only API.",
    )
    parser.add_argument(
        "--target-collection",
        "--collection",
        dest="collection",
        required=True,
        help="Existing Modular RAG target collection to ingest into.",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "settings.yaml"),
        help="Project settings file.",
    )
    parser.add_argument(
        "--base-url",
        help="Override the loopback Zotero Local API base URL from settings.",
    )
    parser.add_argument(
        "--state-db",
        help="Override the collection-scoped Zotero sync state database path.",
    )
    parser.add_argument(
        "--paper-loader",
        action="store_true",
        help="Use the existing GROBID-aware PaperPdfLoader during ingestion.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read Zotero and print the planned actions without ingestion or state writes.",
    )
    parser.add_argument(
        "--manifest-dir",
        default="data/sync_manifests/zotero",
        help="Directory for immutable machine-readable sync manifests.",
    )
    return parser.parse_args(argv)


def _result_payload(
    result: SyncRunResult,
    document_trace_ids: dict[tuple[str, str], str] | None = None,
) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    for entry in result.entries:
        enriched = dict(entry)
        if document_trace_ids:
            trace_id = document_trace_ids.get(
                (entry.get("item_key", ""), entry.get("attachment_key", ""))
            )
            if trace_id:
                enriched["trace_id"] = trace_id
        entries.append(enriched)
    return {
        "added": result.added,
        "updated": result.updated,
        "skipped": result.skipped,
        "inactive": result.inactive,
        "errors": result.errors,
        "entries": entries,
    }


def _plan_payload(plan) -> dict[str, object]:
    counts = {action.value: 0 for action in SyncAction}
    entries = []
    for entry in plan:
        counts[entry.action.value] += 1
        entries.append(
            {
                "item_key": entry.document.item_key,
                "attachment_key": entry.document.attachment_key,
                "action": entry.action.value,
                "reason": entry.reason,
            }
        )
    return {"planned": counts, "entries": entries}


def _document_trace(
    *,
    sync_run_id: str,
    zotero_collection: str,
    target_collection: str,
    action: str,
    reason: str,
    document: SourceDocument,
) -> TraceContext:
    """Create one ingestion trace correlated to its parent Zotero sync run."""
    trace = TraceContext(trace_type="ingestion")
    trace.metadata.update(
        {
            "source": "zotero",
            "scope": "document",
            "sync_run_id": sync_run_id,
            "zotero_collection": zotero_collection,
            "target_collection": target_collection,
            "zotero_item_key": document.item_key,
            "zotero_attachment_key": document.attachment_key,
            "sync_action": action,
            "sync_reason": reason,
        }
    )
    return trace


def _progress_callback(
    *,
    sync_run_id: str,
    trace_id: str,
    attachment_key: str,
):
    """Return the existing pipeline progress callback shape with correlation IDs."""

    def on_progress(stage: str, current: int, total: int) -> None:
        logger.info(
            "Zotero ingestion progress sync_run_id=%s trace_id=%s "
            "attachment_key=%s stage=%s current=%d total=%d",
            sync_run_id,
            trace_id,
            attachment_key,
            stage,
            current,
            total,
        )

    return on_progress


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    sync_run_id = str(uuid.uuid4())
    sync_trace = TraceContext(trace_type="ingestion", trace_id=sync_run_id)
    sync_trace.metadata.update(
        {
            "source": "zotero",
            "scope": "sync_run",
            "sync_run_id": sync_run_id,
            "zotero_collection": args.zotero_collection,
            "target_collection": args.collection,
            "dry_run": args.dry_run,
        }
    )
    collector: TraceCollector | None = None
    try:
        settings = load_settings(args.config)
        zotero = settings.sources.zotero
        if not zotero.enabled:
            raise ValueError(
                "Zotero source is disabled; set sources.zotero.enabled=true before syncing"
            )
        if settings.observability.trace_enabled and not args.dry_run:
            collector = TraceCollector(resolve_path(settings.observability.trace_file))
        base_url = args.base_url or zotero.base_url
        state_db = resolve_path(args.state_db or zotero.sync_state_db)
        client = ZoteroLocalClient(
            base_url=base_url,
            timeout=zotero.request_timeout_seconds,
            allowed_attachment_roots=zotero.allowed_attachment_roots,
        )
        discovery_started = time.monotonic()
        documents = client.list_documents(args.zotero_collection)
        discovery_ms = (time.monotonic() - discovery_started) * 1000.0
        sync_trace.record_stage(
            "zotero_discovery",
            {
                "zotero_collection": args.zotero_collection,
                "source_document_count": len(documents),
            },
            elapsed_ms=discovery_ms,
        )
        state_store = ZoteroSyncStateStore(state_db)
        service = ZoteroSyncService(state_store)
        plan_started = time.monotonic()
        plan = service.plan(documents, args.collection)
        planning_ms = (time.monotonic() - plan_started) * 1000.0
        plan_payload = _plan_payload(plan)
        sync_trace.record_stage(
            "zotero_planning",
            {
                "target_collection": args.collection,
                **plan_payload["planned"],
            },
            elapsed_ms=planning_ms,
        )
        planned_sha256 = {
            entry.document.attachment_key: entry.file_sha256 for entry in plan
        }

        header = {
            "sync_run_id": sync_run_id,
            "run_trace_id": sync_trace.trace_id,
            "zotero_collection": args.zotero_collection,
            "target_collection": args.collection,
            "source_documents": len(documents),
            "state_db": str(state_db),
            "read_only_zotero": True,
            "timings_ms": {
                "source_discovery": round(discovery_ms, 3),
                "planning": round(planning_ms, 3),
            },
        }
        if args.dry_run:
            print(json.dumps({**header, **plan_payload}, ensure_ascii=False, indent=2))
            return 0

        pipeline: IngestionPipeline | None = None
        document_traces: dict[tuple[str, str], TraceContext] = {}
        document_elapsed_ms: dict[tuple[str, str], float] = {}
        for entry in plan:
            key = (entry.document.item_key, entry.document.attachment_key)
            document_traces[key] = _document_trace(
                sync_run_id=sync_run_id,
                zotero_collection=args.zotero_collection,
                target_collection=args.collection,
                action=entry.action.value,
                reason=entry.reason,
                document=entry.document,
            )

        def get_pipeline() -> IngestionPipeline:
            nonlocal pipeline
            if pipeline is None:
                # ``force=True`` is intentional: the independent Zotero state
                # table has already decided a source needs rebuilding.  The legacy
                # global SHA256 table must not skip it because another collection
                # may contain the same bytes.
                pipeline = IngestionPipeline(
                    settings,
                    collection=args.collection,
                    force=True,
                    use_paper_loader=args.paper_loader,
                )
            return pipeline

        def ingest(document: SourceDocument) -> tuple[bool, str | None, str | None]:
            key = (document.item_key, document.attachment_key)
            trace = document_traces[key]
            document_started = time.monotonic()
            try:
                source_metadata = document.to_ingestion_metadata()
                source_metadata["file_sha256"] = planned_sha256[document.attachment_key]
                previous = state_store.get(
                    document.item_key,
                    document.attachment_key,
                    args.collection,
                )

                # Zotero metadata can change without PDF bytes changing.  Update
                # the source snapshot in place and keep vectors/BM25 untouched.
                if previous and previous.file_sha256 == source_metadata["file_sha256"]:
                    metadata_update_started = time.monotonic()
                    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

                    vector_store = VectorStoreFactory.create(
                        settings, collection_name=args.collection
                    )
                    if hasattr(vector_store, "update_metadata"):
                        updated = vector_store.update_metadata(
                            {
                                "source_type": "zotero",
                                "zotero_attachment_key": document.attachment_key,
                            },
                            source_metadata,
                        )
                        if updated:
                            hierarchy = (
                                settings.ingestion.hierarchical_chunking
                                if settings.ingestion
                                else None
                            )
                            if hierarchy and hierarchy.enabled:
                                from src.ingestion.storage.section_store import SectionStore

                                SectionStore(
                                    resolve_path(hierarchy.section_store_db)
                                ).update_document_metadata(
                                    args.collection,
                                    previous.document_id
                                    or f"doc_{previous.file_sha256[:16]}",
                                    source_metadata,
                                )
                            trace.record_stage(
                                "zotero_metadata_update",
                                {"updated": True, "vector_rebuild": False},
                                elapsed_ms=(time.monotonic() - metadata_update_started)
                                * 1000.0,
                            )
                            return True, previous.document_id, None

                active_pipeline = get_pipeline()
                pipeline_result = active_pipeline.run(
                    str(document.attachment.local_path),
                    trace=trace,
                    on_progress=_progress_callback(
                        sync_run_id=sync_run_id,
                        trace_id=trace.trace_id,
                        attachment_key=document.attachment_key,
                    ),
                    source_metadata=source_metadata,
                )
                if pipeline_result.success and pipeline_result.doc_id:
                    if previous and previous.file_sha256 != pipeline_result.doc_id:
                        previous_document_id = f"doc_{previous.file_sha256[:16]}"
                        try:
                            active_pipeline.cleanup_replaced_source(
                                source_filters={
                                    "source_type": "zotero",
                                    "zotero_attachment_key": document.attachment_key,
                                },
                                keep_chunk_ids=pipeline_result.vector_ids,
                                previous_document_id=previous_document_id,
                            )
                        except Exception as exc:
                            return False, None, f"stale source cleanup failed: {exc}"
                return (
                    pipeline_result.success,
                    f"doc_{pipeline_result.doc_id[:16]}" if pipeline_result.doc_id else None,
                    pipeline_result.error,
                )
            finally:
                document_elapsed_ms[key] = (
                    time.monotonic() - document_started
                ) * 1000.0

        try:
            execution_started = time.monotonic()
            result = service.execute(
                plan,
                ingest,
                target_collection=args.collection,
            )
            execution_ms = (time.monotonic() - execution_started) * 1000.0
        finally:
            if pipeline is not None:
                pipeline.close()
        result_by_key = {
            (entry["item_key"], entry["attachment_key"]): entry
            for entry in result.entries
        }
        document_trace_ids = {
            key: trace.trace_id for key, trace in document_traces.items()
        }
        for entry in plan:
            key = (entry.document.item_key, entry.document.attachment_key)
            outcome = result_by_key[key]
            trace = document_traces[key]
            trace.record_stage(
                "zotero_sync",
                {
                    "action": outcome["action"],
                    "status": outcome["status"],
                    "reason": outcome["reason"],
                    "error": outcome.get("error", ""),
                },
                elapsed_ms=document_elapsed_ms.get(key, 0.0),
            )
            if collector is not None:
                collector.collect(trace)
        sync_trace.record_stage(
            "zotero_execution",
            {
                "added": result.added,
                "updated": result.updated,
                "skipped": result.skipped,
                "inactive": result.inactive,
                "errors": result.errors,
                "document_trace_count": len(document_traces),
                "document_trace_ids": list(document_trace_ids.values()),
            },
            elapsed_ms=execution_ms,
        )
        finished_at = datetime.now(timezone.utc)
        manifest_dir = resolve_path(args.manifest_dir)
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_name = started_at.strftime("%Y%m%dT%H%M%S.%fZ.json")
        manifest_path = manifest_dir / manifest_name
        payload = {
            **header,
            **_result_payload(result, document_trace_ids),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "manifest_path": str(manifest_path),
            "timings_ms": {
                **header["timings_ms"],
                "execution": round(execution_ms, 3),
                "total": round((time.monotonic() - started_clock) * 1000.0, 3),
            },
        }
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        sync_trace.metadata["manifest_path"] = str(manifest_path)
        sync_trace.record_stage(
            "zotero_manifest",
            {"manifest_path": str(manifest_path), "written": True},
        )
        if collector is not None:
            collector.collect(sync_trace)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if result.errors else 0
    except (OSError, ValueError, ZoteroClientError) as exc:
        sync_trace.record_stage(
            "zotero_sync_error",
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
        if collector is not None:
            collector.collect(sync_trace)
        print(
            f"Zotero sync failed: sync_run_id={sync_run_id} "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
