import json
from unittest.mock import Mock

import fitz
import httpx
import pytest

from src.libs.loader.mineru_agent import MinerUAgentClient, markdown_structure
from src.libs.loader.pdf_loader import PaperPdfLoader


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "paper.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 50), "A paper")
        doc.save(path)
    return path


def test_upload_poll_and_cache_without_authorization(pdf, tmp_path, monkeypatch):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        assert "authorization" not in request.headers
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "task_id": "task1", "file_url": "https://upload.example/paper"}})
        if request.method == "PUT":
            assert request.read().startswith(b"%PDF")
            return httpx.Response(200)
        if request.url.path.endswith("task1"):
            return httpx.Response(200, json={"code": 0, "data": {
                "state": "done", "markdown_url": "https://results.example/paper.md"}})
        return httpx.Response(200, text="# Paper\n\nText")
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    client = MinerUAgentClient(tmp_path / "cache", poll_interval=0)
    text, first = client.parse(pdf)
    assert not first["cache_hit"] and text.startswith("# Paper")
    text2, second = client.parse(pdf)
    assert text2 == text and second["cache_hit"]
    assert len(calls) == 4
    task = json.loads(next((tmp_path / "cache").glob("*/task.json")).read_text())
    assert "file_url" not in task


def test_failed_remote_parse_never_falls_back(pdf, tmp_path, monkeypatch):
    client = Mock()
    client.parse.side_effect = RuntimeError("MinerU unavailable")
    monkeypatch.setattr("src.libs.loader.mineru_agent.MinerUAgentClient", lambda *a: client)
    with pytest.raises(RuntimeError, match="MinerU unavailable"):
        PaperPdfLoader(mineru_cache_dir=tmp_path).load(pdf)


def test_markdown_preserves_math_and_separates_references():
    text, meta = markdown_structure(
        "# Paper\n\nExplicit anisotropy—The lattice matters.\n\n"
        "$$x = y$$\n\n<!-- image-->\n\nFIG. 3. Actual caption.\n\n"
        "[1] Reference one.\n\n## End Matter\n\nAppendix text."
    )
    assert "$$x = y$$" in text
    assert "## Explicit anisotropy" in text
    assert "<!-- image-->" not in text
    assert meta["paper_figures"][0]["number"] == 3
    assert meta["references_raw"] == "[1] Reference one."
    assert all("Reference one" not in str(s) for s in meta["paper_sections"])
    assert meta["paper_sections"][-1]["heading"] == "End Matter"


def test_too_many_pages_rejected_before_upload(tmp_path):
    path = tmp_path / "long.pdf"
    with fitz.open() as doc:
        for _ in range(201):
            doc.new_page()
        doc.save(path)
    with pytest.raises(ValueError, match="1-200 pages"):
        MinerUAgentClient(tmp_path / "cache").parse(path)


def test_segmented_pdf_preserves_pages_identity_and_resumes(tmp_path, monkeypatch):
    import hashlib

    path = tmp_path / "long.pdf"
    with fitz.open() as doc:
        for i in range(23):
            doc.new_page().insert_text((40, 40), f"Unique physical page {i + 1}")
        doc.save(path)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = MinerUAgentClient(tmp_path / "cache")
    parts = client.prepare_parts(path)
    assert [(p["page_start"], p["page_end"]) for p in parts] == [(1, 20), (21, 23)]
    with fitz.open(path) as source:
        for part in parts:
            with fitz.open(part["path"]) as pdf:
                for offset, page in enumerate(pdf):
                    original = source[part["page_start"] - 1 + offset]
                    assert page.get_text() == original.get_text()
                    assert page.get_pixmap(matrix=fitz.Matrix(.3, .3)).samples == original.get_pixmap(matrix=fitz.Matrix(.3, .3)).samples
    calls = []

    def parse(part):
        calls.append(part.name)
        return part.stem, {"task_id": part.stem, "cache_hit": True, "markdown_path": "cached.md"}

    monkeypatch.setattr(client, "_parse_single", parse)
    markdown, meta = client.parse(path)
    assert markdown.index("0001-0020") < markdown.index("0021-0023")
    assert meta["sha256"] == original_hash and meta["page_count"] == 23
    assert meta["task_id"] is None and len(meta["task_ids"]) == 2
    assert meta["cache_hit"] and len(calls) == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


def test_byte_partition_and_failed_part_never_yields_partial_document(tmp_path, monkeypatch):
    path = tmp_path / "pages.pdf"
    with fitz.open() as doc:
        for i in range(4):
            doc.new_page().insert_text((40, 40), f"Page {i}")
        doc.save(path)
    client = MinerUAgentClient(tmp_path / "cache")
    client.request_max_pages = 2
    def fail(part):
        if "0003" in part.name:
            raise RuntimeError("second segment failed")
        return "First half", {"task_id": "first", "cache_hit": True, "markdown_path": "one.md"}
    monkeypatch.setattr(client, "_parse_single", fail)
    with pytest.raises(RuntimeError, match="second segment"):
        client.parse(path)
    assert not list((tmp_path / "cache").glob("*/paper.md"))


def test_caption_number_used_for_reference_mapping():
    from src.ingestion.chunking.document_chunker import DocumentChunker
    assert DocumentChunker._build_asset_ref_map([{"id": "fig_3", "number": 3}]) == {3: "fig_3"}


def test_resumes_uploaded_task_and_persists_failure(pdf, tmp_path, monkeypatch):
    import hashlib
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    folder = tmp_path / "cache" / digest
    folder.mkdir(parents=True)
    (folder / "task.json").write_text(json.dumps({"task_id": "old", "state": "uploaded"}))
    calls = []
    def handle(request):
        calls.append(request.method)
        assert request.method == "GET"
        return httpx.Response(200, json={"code": 0, "data": {"state": "failed", "err_msg": "Parse error"}})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    client = MinerUAgentClient(tmp_path / "cache", poll_interval=0)
    with pytest.raises(RuntimeError, match="Parse error"):
        client.parse(pdf)
    with pytest.raises(RuntimeError, match="Parse error"):
        client.parse(pdf)
    assert calls == ["GET"]


def test_mineru_text_and_provenance_feed_real_chunker(pdf, tmp_path, monkeypatch):
    from src.core.settings import load_settings
    from src.ingestion.chunking.document_chunker import DocumentChunker
    markdown = "# Paper\n\nMechanism—See Figure 3 for the result.\n\nFIG. 3. Active clock equations."
    client = Mock()
    client.parse.return_value = (markdown, {"sha256": "abc123", "task_id": "test",
        "cache_hit": False, "markdown_path": "raw.md", "page_count": 1})
    monkeypatch.setattr("src.libs.loader.mineru_agent.MinerUAgentClient", lambda *a: client)
    doc = PaperPdfLoader(mineru_cache_dir=tmp_path).load(pdf)
    assert "Active clock equations" in doc.text
    assert "grobid_sections" not in doc.metadata
    chunks = DocumentChunker(load_settings()).split_document(doc)
    assert any(c.metadata.get("figure_id") == "fig_3" for c in chunks)
    assert any("fig_3" in c.metadata.get("linked_figures", []) for c in chunks)
    assert all(c.metadata["parser"] == "mineru_agent" for c in chunks)
