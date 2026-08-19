"""Regression tests for Zotero sync correlation and ingestion tracing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from scripts import sync_zotero
from src.integrations.zotero.models import SourceAttachment, SourceDocument


def _document(tmp_path: Path) -> SourceDocument:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf-content")
    return SourceDocument(
        item_key="ITEM123",
        attachment=SourceAttachment(
            key="ATTACH123",
            local_path=pdf,
            version="2",
        ),
        title="Observable paper",
        creators=("Ada Lovelace",),
        year="2026",
        collection_keys=("ZCOLL",),
        item_version="3",
    )


def _settings(tmp_path: Path):
    return SimpleNamespace(
        sources=SimpleNamespace(
            zotero=SimpleNamespace(
                enabled=True,
                base_url="http://127.0.0.1:23119",
                request_timeout_seconds=10,
                allowed_attachment_roots=(),
                sync_state_db=str(tmp_path / "zotero-state.sqlite3"),
            )
        ),
        observability=SimpleNamespace(
            trace_enabled=True,
            trace_file=str(tmp_path / "traces.jsonl"),
        ),
        ingestion=SimpleNamespace(
            hierarchical_chunking=SimpleNamespace(enabled=False),
        ),
    )


class _RecordingCollector:
    collected = []
    paths = []

    def __init__(self, traces_path: Path) -> None:
        self.paths.append(Path(traces_path))

    def collect(self, trace) -> None:
        trace.finish()
        self.collected.append(trace)


class _FakePipeline:
    calls = []
    succeeds = True

    def __init__(self, settings, collection, force, use_paper_loader) -> None:
        self.collection = collection
        self.closed = False

    def run(self, file_path, trace=None, on_progress=None, source_metadata=None):
        assert trace is not None
        assert on_progress is not None
        on_progress("load", 1, 6)
        trace.record_stage("load", {"document_id": "doc-test"}, elapsed_ms=1.0)
        self.calls.append(
            {
                "file_path": file_path,
                "trace": trace,
                "on_progress": on_progress,
                "source_metadata": dict(source_metadata),
            }
        )
        return SimpleNamespace(
            success=self.succeeds,
            doc_id="a" * 64 if self.succeeds else None,
            vector_ids=["chunk-existing-contract"] if self.succeeds else [],
            error=None if self.succeeds else "embedding unavailable",
        )

    def cleanup_replaced_source(self, **kwargs) -> None:
        raise AssertionError("a new source must not run replacement cleanup")

    def close(self) -> None:
        self.closed = True


def _install_fakes(
    monkeypatch,
    tmp_path: Path,
    document: SourceDocument,
    *,
    use_real_collector: bool = False,
) -> None:
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def list_documents(self, collection_key):
            assert collection_key == "ZCOLL"
            return [document]

    _RecordingCollector.collected = []
    _RecordingCollector.paths = []
    _FakePipeline.calls = []
    _FakePipeline.succeeds = True
    monkeypatch.setattr(sync_zotero, "load_settings", lambda _: _settings(tmp_path))
    monkeypatch.setattr(sync_zotero, "ZoteroLocalClient", FakeClient)
    if not use_real_collector:
        monkeypatch.setattr(sync_zotero, "TraceCollector", _RecordingCollector)
    monkeypatch.setattr(sync_zotero, "IngestionPipeline", _FakePipeline)


def _args(tmp_path: Path, *, dry_run: bool = False) -> list[str]:
    args = [
        "--collection-key",
        "ZCOLL",
        "--target-collection",
        "papers-v2",
        "--manifest-dir",
        str(tmp_path / "manifests"),
    ]
    if dry_run:
        args.append("--dry-run")
    return args


def test_sync_correlates_run_document_trace_progress_and_manifest(
    tmp_path: Path,
    monkeypatch,
    capsys,
    caplog,
) -> None:
    document = _document(tmp_path)
    _install_fakes(monkeypatch, tmp_path, document, use_real_collector=True)
    caplog.set_level(logging.INFO, logger=sync_zotero.__name__)

    assert sync_zotero.main(_args(tmp_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    persisted_traces = [
        json.loads(line)
        for line in (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    run_trace = next(
        trace for trace in persisted_traces if trace["metadata"]["scope"] == "sync_run"
    )
    document_trace = next(
        trace for trace in persisted_traces if trace["metadata"]["scope"] == "document"
    )

    assert len(persisted_traces) == 2
    assert payload["sync_run_id"] == run_trace["trace_id"]
    assert payload["run_trace_id"] == run_trace["trace_id"]
    assert document_trace["metadata"]["sync_run_id"] == run_trace["trace_id"]
    assert document_trace["metadata"]["zotero_attachment_key"] == "ATTACH123"
    assert payload["entries"][0]["trace_id"] == document_trace["trace_id"]
    assert _FakePipeline.calls[0]["trace"].trace_id == document_trace["trace_id"]
    assert _FakePipeline.calls[0]["source_metadata"]["zotero_attachment_key"] == "ATTACH123"
    assert "chunk-existing-contract" not in _FakePipeline.calls[0]["source_metadata"].values()

    document_stages = [stage["stage"] for stage in document_trace["stages"]]
    run_stages = [stage["stage"] for stage in run_trace["stages"]]
    assert document_stages == ["load", "zotero_sync"]
    assert run_stages == [
        "zotero_discovery",
        "zotero_planning",
        "zotero_execution",
        "zotero_manifest",
    ]
    execution_stage = next(
        stage["data"] for stage in run_trace["stages"] if stage["stage"] == "zotero_execution"
    )
    assert execution_stage["document_trace_ids"] == [document_trace["trace_id"]]
    assert "sync_run_id=" in caplog.text
    assert "attachment_key=ATTACH123 stage=load current=1 total=6" in caplog.text

    manifest_path = Path(payload["manifest_path"])
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["manifest_path"] == str(manifest_path)
    assert persisted["sync_run_id"] == run_trace["trace_id"]
    assert persisted["entries"][0]["trace_id"] == document_trace["trace_id"]


def test_skipped_document_still_gets_correlated_trace(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    document = _document(tmp_path)
    _install_fakes(monkeypatch, tmp_path, document)

    assert sync_zotero.main(_args(tmp_path)) == 0
    capsys.readouterr()
    first_call_count = len(_FakePipeline.calls)
    _RecordingCollector.collected = []

    assert sync_zotero.main(_args(tmp_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    run_trace = next(
        trace for trace in _RecordingCollector.collected if trace.metadata["scope"] == "sync_run"
    )
    document_trace = next(
        trace for trace in _RecordingCollector.collected if trace.metadata["scope"] == "document"
    )
    sync_stage = document_trace.get_stage_data("zotero_sync")

    assert len(_FakePipeline.calls) == first_call_count
    assert payload["skipped"] == 1
    assert payload["entries"][0]["trace_id"] == document_trace.trace_id
    assert document_trace.metadata["sync_run_id"] == run_trace.trace_id
    assert sync_stage["status"] == "skipped"
    assert sync_stage["action"] == "skip"


def test_dry_run_reports_sync_run_id_without_writing_trace_or_manifest(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    document = _document(tmp_path)
    _install_fakes(monkeypatch, tmp_path, document)

    assert sync_zotero.main(_args(tmp_path, dry_run=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["sync_run_id"] == payload["run_trace_id"]
    assert payload["planned"]["add"] == 1
    assert _RecordingCollector.paths == []
    assert _RecordingCollector.collected == []
    assert _FakePipeline.calls == []
    assert not (tmp_path / "manifests").exists()


def test_failed_ingestion_persists_correlated_error_trace_and_manifest(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    document = _document(tmp_path)
    _install_fakes(monkeypatch, tmp_path, document)
    _FakePipeline.succeeds = False

    assert sync_zotero.main(_args(tmp_path)) == 1

    payload = json.loads(capsys.readouterr().out)
    run_trace = next(
        trace for trace in _RecordingCollector.collected if trace.metadata["scope"] == "sync_run"
    )
    document_trace = next(
        trace for trace in _RecordingCollector.collected if trace.metadata["scope"] == "document"
    )
    sync_stage = document_trace.get_stage_data("zotero_sync")

    assert payload["errors"] == 1
    assert payload["entries"][0]["status"] == "error"
    assert payload["entries"][0]["error"] == "embedding unavailable"
    assert payload["entries"][0]["trace_id"] == document_trace.trace_id
    assert document_trace.metadata["sync_run_id"] == run_trace.trace_id
    assert sync_stage["status"] == "error"
    assert sync_stage["error"] == "embedding unavailable"
    assert run_trace.get_stage_data("zotero_execution")["errors"] == 1

    persisted = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert persisted["entries"][0]["trace_id"] == document_trace.trace_id
    assert persisted["errors"] == 1
