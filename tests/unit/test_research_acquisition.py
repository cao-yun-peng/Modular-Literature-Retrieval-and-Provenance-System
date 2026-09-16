"""Network-free tests for acquisition provenance, write gates and failure isolation."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.integrations.arxiv import ArxivClient, LiteratureAcquirer, Paper, validate_url
from src.integrations.zotero.connector import ZoteroConnector


@pytest.fixture
def paper():
    return Paper("2409.13740v1", "Research\nTI  - injected", ["A Author"], "2024-09-20", "Abstract")


def test_arxiv_feed_preserves_dates_and_deduplicates_versions():
    entry = "<entry><id>http://arxiv.org/abs/2409.13740v1</id><title>Paper</title><published>2024-09-20</published><summary>Abstract</summary><author><name>A</name></author></entry>"
    xml = f'<feed xmlns="http://www.w3.org/2005/Atom">{entry}{entry.replace("v1", "v2")}</feed>'
    papers = ArxivClient.parse_feed(xml.encode())
    assert len(papers) == 1
    assert papers[0].metadata()["year"] == "2024"
    assert papers[0].pdf_url == "https://arxiv.org/pdf/2409.13740v1"


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/pdf/x",
        "https://arxiv.org.evil.test/a",
        "https://127.0.0.1/x",
        "file:///etc/passwd",
        "https://arxiv.org:8080/x",
        "https://user@arxiv.org/pdf/x",
    ],
)
def test_download_url_allowlist(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_xml_entities_and_invalid_ids_rejected():
    with pytest.raises(ValueError):
        ArxivClient.parse_feed(b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///a">]><x/>')
    with pytest.raises(ValueError):
        Paper("../../secret", "x", [], "", "")


def test_download_rejects_html_and_keeps_valid_cache(tmp_path, paper):
    client = ArxivClient()
    client._read = Mock(return_value=b"<html>not PDF</html>")
    with pytest.raises(ValueError, match="not a PDF"):
        client.download(paper, tmp_path)
    assert not list(tmp_path.glob("*.pdf"))
    client._read.return_value = b"%PDF-test"
    path = client.download(paper, tmp_path)
    assert client.download(paper, tmp_path) == path
    assert client._read.call_count == 2


def test_transport_size_limit(monkeypatch):
    monkeypatch.setattr("src.integrations.arxiv.time.sleep", lambda _: None)
    response = io.BytesIO(b"123456")
    response.geturl = lambda: "https://arxiv.org/pdf/2409.13740"
    client = ArxivClient(opener=SimpleNamespace(open=lambda *a, **k: response))
    with pytest.raises(ValueError, match="size limit"):
        client._read(response.geturl(), 5)


def test_acquisition_indexes_full_pdf_and_keeps_ris_when_zotero_fails(tmp_path, paper):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-fixture")
    client = SimpleNamespace(search=lambda *a: [paper], download=lambda *a: path)
    ingest = Mock(return_value=SimpleNamespace(success=True))
    zotero = SimpleNamespace(import_paper=Mock(side_effect=ConnectionError))
    records = LiteratureAcquirer(client, tmp_path, ingest, zotero=zotero).acquire("x", "papers", 1)
    assert records[0]["rag_status"] == "indexed"
    assert records[0]["zotero_status"] == "failed_or_uncertain"
    assert len(records[0]["sha256"]) == 64
    assert ingest.call_args.args[1]["source_type"] == "arxiv"
    assert "L1  - https://arxiv.org/pdf/" in (tmp_path / "references.ris").read_text()
    assert (tmp_path / "references.ris").read_text().count("\nTI  -") == 1


def test_zotero_write_gates_before_request(tmp_path, paper):
    connector = ZoteroConnector(state_path=tmp_path / "state.db")
    connector._post = Mock()
    with pytest.raises(PermissionError):
        connector.import_paper(paper)
    connector._post.assert_not_called()
    connector.allow_write, connector.expected_target = True, "C1"
    connector._post.return_value = {
        "id": 2,
        "libraryID": 1,
        "editable": True,
        "libraryEditable": True,
    }
    with pytest.raises(PermissionError):
        connector.import_paper(paper)
    assert connector._post.call_count == 1


def test_zotero_accepted_import_is_idempotent(tmp_path, paper):
    connector = ZoteroConnector(
        allow_write=True, expected_target="L1", state_path=tmp_path / "state.db"
    )
    target = {"id": None, "libraryID": 1, "editable": True, "libraryEditable": True}
    connector._post = Mock(side_effect=[target, [{"key": "ABCDEFGH"}], target])
    result = connector.import_paper(paper)
    assert result["attachment_status"] == "not_verified"
    assert connector.import_paper(paper)["status"] == "already_imported"
    assert connector._post.call_count == 3


def test_uncertain_zotero_import_is_not_retried(tmp_path, paper):
    connector = ZoteroConnector(
        allow_write=True, expected_target="L1", state_path=tmp_path / "state.db"
    )
    target = {"id": None, "libraryID": 1, "editable": True, "libraryEditable": True}
    connector._post = Mock(side_effect=[target, TimeoutError(), target])
    with pytest.raises(TimeoutError):
        connector.import_paper(paper)
    with pytest.raises(RuntimeError, match="uncertain"):
        connector.import_paper(paper)
    assert connector._post.call_count == 3
