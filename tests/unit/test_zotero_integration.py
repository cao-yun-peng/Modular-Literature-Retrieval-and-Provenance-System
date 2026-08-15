"""Unit tests for the optional, read-only Zotero source integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.integrations.zotero.client import ZoteroLocalClient
from src.integrations.zotero.models import SourceAttachment, SourceDocument
from src.integrations.zotero.state import ZoteroSyncState, ZoteroSyncStateStore
from src.integrations.zotero.sync_service import SyncAction, ZoteroSyncService


def _source_document(tmp_path: Path, *, version: str = "1", content: bytes = b"pdf") -> SourceDocument:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(content)
    return SourceDocument(
        item_key="ITEM123",
        attachment=SourceAttachment(key="ATTACH123", local_path=pdf, version=version),
        title="A paper",
        creators=("Ada Lovelace",),
        year="2025",
        doi="10.1000/example",
        collection_keys=("COLL",),
        tags=("rag",),
        item_version=version,
        citation_key="lovelace_2025",
    )


def test_source_document_generates_namespaced_ingestion_metadata(tmp_path: Path) -> None:
    metadata = _source_document(tmp_path).to_ingestion_metadata()

    assert metadata["source_type"] == "zotero"
    assert metadata["zotero_item_key"] == "ITEM123"
    assert metadata["zotero_attachment_key"] == "ATTACH123"
    assert metadata["citation_key"] == "lovelace_2025"
    assert metadata["zotero_creators"] == ["Ada Lovelace"]


def test_pipeline_source_metadata_preserves_loader_owned_contracts(tmp_path: Path) -> None:
    from src.core.types import Document
    from src.ingestion.source_metadata import attach_source_metadata

    document = Document(
        id="doc-1",
        text="content",
        metadata={"source_path": "loader.pdf", "images": []},
    )
    attached = attach_source_metadata(
        document, _source_document(tmp_path).to_ingestion_metadata()
    )

    assert attached["zotero_item_key"] == "ITEM123"
    assert document.metadata["source_path"] == "loader.pdf"
    with pytest.raises(ValueError, match="reserved key"):
        attach_source_metadata(document, {"source_path": "bad.pdf"})


def test_sync_plan_is_collection_scoped_and_idempotent(tmp_path: Path) -> None:
    store = ZoteroSyncStateStore(tmp_path / "state.sqlite3")
    service = ZoteroSyncService(store)
    document = _source_document(tmp_path)

    first = service.plan([document], "papers")
    assert first[0].action == SyncAction.ADD

    result = service.execute(first, lambda _: (True, "doc-1", None))
    assert (result.added, result.updated, result.skipped, result.errors) == (1, 0, 0, 0)

    second = service.plan([document], "papers")
    assert second[0].action == SyncAction.SKIP
    other_collection = service.plan([document], "other-papers")
    assert other_collection[0].action == SyncAction.ADD


def test_sync_plan_updates_when_attachment_content_changes(tmp_path: Path) -> None:
    store = ZoteroSyncStateStore(tmp_path / "state.sqlite3")
    service = ZoteroSyncService(store)
    document = _source_document(tmp_path, content=b"first")
    service.execute(service.plan([document], "papers"), lambda _: (True, "doc-1", None))

    document.attachment.local_path.write_bytes(b"changed")
    assert service.plan([document], "papers")[0].action == SyncAction.UPDATE


def test_sync_marks_missing_attachments_inactive_without_deleting(tmp_path: Path) -> None:
    store = ZoteroSyncStateStore(tmp_path / "state.sqlite3")
    store.save(
        ZoteroSyncState(
            item_key="OLD",
            attachment_key="OLDATTACH",
            target_collection="papers",
            source_version="1:1",
            file_sha256="abc",
            document_id="doc-old",
            status="synced",
            last_synced_at=store.now(),
        )
    )

    result = ZoteroSyncService(store).execute(
        [],
        lambda _: (True, None, None),
        target_collection="papers",
    )

    assert result.inactive == 1
    assert store.get("OLD", "OLDATTACH", "papers").status == "inactive"


def test_sync_execution_persists_ingestion_errors(tmp_path: Path) -> None:
    store = ZoteroSyncStateStore(tmp_path / "state.sqlite3")
    service = ZoteroSyncService(store)
    entry = service.plan([_source_document(tmp_path)], "papers")[0]

    result = service.execute([entry], lambda _: (False, None, "embedding unavailable"))

    assert result.errors == 1
    state = store.get("ITEM123", "ATTACH123", "papers")
    assert state is not None
    assert state.status == "error"
    assert state.error_message == "embedding unavailable"


class _StubZoteroClient(ZoteroLocalClient):
    def __init__(self, pdf: Path) -> None:
        super().__init__()
        self.pdf = pdf

    def _get_json(self, path, parameters=None):
        if path.endswith("/collections/COLL/items/top"):
            return ([{"key": "ITEM123", "version": 7, "data": {"itemType": "journalArticle", "title": "Paper", "date": "2024-01-01", "DOI": "10.1/x", "collections": ["COLL"], "creators": [{"firstName": "Ada", "lastName": "Lovelace"}], "tags": [{"tag": "rag"}]}}], {})
        if path.endswith("/items/ITEM123/children"):
            return ([{"key": "ATTACH123", "version": 8, "data": {"itemType": "attachment", "contentType": "application/pdf"}}], {})
        raise AssertionError(path)

    def _get_text(self, path, parameters=None):
        if path.endswith("/items/ATTACH123/file/view/url"):
            return (self.pdf.as_uri(), {})
        raise AssertionError(path)


def test_local_client_maps_zotero_items_and_pdf_attachment(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")

    documents = _StubZoteroClient(pdf).list_documents("COLL")

    assert len(documents) == 1
    assert documents[0].item_key == "ITEM123"
    assert documents[0].attachment_key == "ATTACH123"
    assert documents[0].title == "Paper"
    assert documents[0].creators == ("Ada Lovelace",)


class _PlainTextResponse:
    def __init__(self, body: str) -> None:
        self.body = body.encode("utf-8")
        self.headers = {"Content-Type": "text/plain"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_local_client_accepts_plain_text_attachment_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    response = _PlainTextResponse(f"  {pdf.as_uri()}\n")
    monkeypatch.setattr(
        "src.integrations.zotero.client.urlopen",
        lambda request, timeout: response,
    )

    path = ZoteroLocalClient().get_attachment_file_path("ATTACH123")

    assert path == pdf.resolve()


def test_local_client_rejects_non_loopback_base_url() -> None:
    with pytest.raises(ValueError, match="loopback"):
        ZoteroLocalClient("http://example.com:23119")
