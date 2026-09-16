"""Token integrity, full-input embedding, image provenance and legacy compatibility."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from src.core.retrieval_text import image_annotations, retrieval_text
from src.core.settings import load_settings
from src.core.types import Chunk, Document
from src.ingestion.chunking.document_chunker import DocumentChunker
from src.ingestion.transform.image_captioner import ImageCaptioner
from src.libs.dashscope_client import DashScopeError
from src.libs.embedding.dashscope_embedding import DashScopeEmbedding
from src.libs.llm.base_llm import ChatResponse
from src.libs.llm.base_vision_llm import ImageInput
from src.libs.llm.dashscope_vision_llm import DashScopeVisionLLM
from src.libs.splitter.token_splitter import TokenSplitter


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-not-a-real-key")
    cfg = load_settings("config/settings.dashscope.yaml")
    return replace(
        cfg, vector_store=replace(cfg.vector_store, persist_directory=str(tmp_path / "chroma"))
    )


@pytest.mark.parametrize(
    "text",
    ["中文🙂数学α≠β\n" * 200, "Academic mechanisms and conditions.\n\n" * 400],
    ids=["unicode", "paragraphs"],
)
def test_token_limits_preserve_every_character(settings, text):
    cfg = replace(settings, ingestion=replace(settings.ingestion, chunk_size=100, chunk_overlap=15))
    splitter = TokenSplitter(cfg)
    spans = splitter.split_spans(text)
    assert len(spans) > 6  # no target number of paper parts
    covered = [False] * len(text)
    for start, end in spans:
        assert splitter.count(text[start:end]) <= 100
        assert "\ufffd" not in text[start:end]
        covered[start:end] = [True] * (end - start)
    assert all(covered)


def test_token_chunks_use_physical_pages_and_keep_source(settings):
    text = "Methods and conditions. " * 30
    doc = Document(
        "paper",
        text,
        {
            "source_path": "p.pdf",
            "paper_mode": True,
            "page_ranges": [{"page": 1, "start": 0, "end": len(text)}],
            "images": [{"id": "fig", "page": 1, "path": "fig.png"}],
        },
    )
    chunks = DocumentChunker(settings).split_document(doc)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].metadata["page_start"] == 1
    assert chunks[0].metadata["image_refs"] == ["fig"]
    assert chunks[0].metadata["token_count"] <= 2500


def test_dashscope_batches_keep_long_inputs_and_order(settings, monkeypatch):
    embedding = DashScopeEmbedding(settings)
    calls = []

    def fake(path, payload, identity, trace):
        calls.append(payload)
        return {
            "data": [
                {"index": i, "embedding": [float(i)] * 1024}
                for i in reversed(range(len(payload["input"])))
            ]
        }

    monkeypatch.setattr(embedding.client, "post", fake)
    texts = ["x" * 7000] * 23
    vectors = embedding.embed(texts)
    assert [len(c["input"]) for c in calls] == [10, 10, 3]
    assert calls[0]["input"][0] == texts[0]
    assert len(vectors) == 23 and vectors[1][0] == 1
    monkeypatch.setattr(
        embedding.client, "post", lambda *a: {"data": [{"index": 0, "embedding": [1]}]}
    )
    with pytest.raises(DashScopeError):
        embedding.embed(["one"])


def test_vision_content_cache_and_provenance(settings, tmp_path, monkeypatch):
    path = tmp_path / "figure.png"
    Image.new("RGB", (64, 64), "white").save(path)
    provider = DashScopeVisionLLM(settings)
    calls = []

    def fake(endpoint, payload, identity, trace):
        calls.append(payload)
        return {
            "id": "r1",
            "usage": {"total_tokens": 50},
            "choices": [{"finish_reason": "stop", "message": {"content": "A blank white image."}}],
        }

    monkeypatch.setattr(provider.client, "post", fake)
    first = provider.chat_with_image("Read image", ImageInput(path=path))
    second = provider.chat_with_image("Read image", ImageInput(path=path))
    assert len(calls) == 1
    assert first.raw_response["status"] == "model_generated_unreviewed"
    assert second.usage["total_tokens"] == 0 and second.raw_response["cache_hit"]
    provider.chat_with_image("Different prompt", ImageInput(path=path))
    assert len(calls) == 2
    assert "test-not-a-real-key" not in (provider.client.root / "calls.jsonl").read_text()


def test_captioning_enriches_retrieval_without_rewriting_source(settings, tmp_path):
    path = tmp_path / "figure.png"
    Image.new("RGB", (128, 128), "white").save(path)
    fake = SimpleNamespace(
        chat_with_image=lambda **kw: ChatResponse(
            "A blue vortex with arrows.", "fixture-vl", {"total_tokens": 9}, {}
        )
    )
    chunk = Chunk(
        "c",
        "Source experiment.",
        {
            "source_path": "p.pdf",
            "preserve_source_text": True,
            "images": [{"id": "fig", "path": str(path), "page": 3}],
            "image_refs": ["fig"],
        },
    )
    output = ImageCaptioner(settings, llm=fake).transform([chunk])[0]
    assert output.text == "Source experiment."
    assert "blue vortex" in retrieval_text(output)
    annotations = image_annotations(output.metadata)
    assert annotations[0]["page"] == 3
    assert annotations[0]["is_source_text"] is False
    assert annotations[0]["status"] == "model_generated_unreviewed"


def test_visual_text_never_counts_as_pdf_gold_but_consumes_budget():
    from src.observability.evaluation.benchmark_scoring import expanded, span_covered

    evidence = [
        {
            "paper_id": "p",
            "text": "original",
            "image_annotations": [{"description": "The exact golden evidence appears here."}],
        }
    ]
    output = expanded(evidence, budget=18)
    assert sum(len(e["text"]) for e in output) == 18
    assert not span_covered(
        {"paper_id": "p", "quote": "The exact golden evidence appears here."}, expanded(evidence)
    )


def test_service_bundle_exposes_marked_visual_annotations():
    from src.core.response.evidence_bundle import EvidenceBundleBuilder
    from src.core.types import RetrievalResult

    metadata = {
        "captions_separate": True,
        "image_captions": json.dumps({"f": "A vortex."}),
        "image_caption_records": json.dumps(
            {"f": {"page": 2, "status": "model_generated_unreviewed"}}
        ),
    }
    result = EvidenceBundleBuilder()._evidence(
        RetrievalResult(chunk_id="c", text="Original source", score=1.0, metadata=metadata)
    )
    assert result["text"] == "Original source"
    assert result["image_annotations"][0]["is_source_text"] is False


@pytest.mark.parametrize("use_llm", [False, True])
def test_refiner_preserves_exact_source_in_both_paths(settings, use_llm):
    from src.ingestion.transform.chunk_refiner import ChunkRefiner

    cfg = replace(
        settings, ingestion=replace(settings.ingestion, chunk_refiner={"use_llm": use_llm})
    )
    source = "  Equation: alpha != beta.\n\n\nPage 1  "
    chunk = Chunk("c", source, {"source_path": "p.pdf", "preserve_source_text": True})
    refiner = ChunkRefiner(cfg, llm=SimpleNamespace())
    result = refiner.transform([chunk])[0]
    assert result.text == source
    assert result.metadata["refined_by"] == "source_preserved"


def test_caption_supplement_budget_includes_formatting():
    from src.libs.splitter.token_splitter import get_tokenizer

    chunk = Chunk(
        "c",
        "Original.",
        {
            "source_path": "p.pdf",
            "captions_separate": True,
            "image_captions": {str(i): "Visible blue shapes. " * 100 for i in range(70)},
        },
    )
    supplement = retrieval_text(chunk)[len(chunk.text) + 2 :]
    assert len(get_tokenizer().encode(supplement)) <= 1000


def test_small_rasters_have_explicit_skip_status(settings, tmp_path):
    image = tmp_path / "tiny.png"
    Image.new("RGB", (10, 10), "white").save(image)

    def should_not_call(**kw):
        raise AssertionError("Small raster should not be sent to the model")

    chunk = Chunk(
        "c",
        "Source.",
        {
            "source_path": "p.pdf",
            "preserve_source_text": True,
            "image_refs": ["small"],
            "images": [{"id": "small", "page": 1, "path": str(image)}],
        },
    )
    result = ImageCaptioner(
        settings, llm=SimpleNamespace(chat_with_image=should_not_call)
    ).transform([chunk])[0]
    assert result.metadata["image_caption_records"]["small"]["status"] == "skipped_small_raster"
    assert result.text == "Source."


def test_complete_pipeline_keeps_source_and_indexes_visual_notes(settings, tmp_path, monkeypatch):
    from src.ingestion.pipeline import IngestionPipeline
    from src.libs.dashscope_client import DashScopeClient
    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

    paper = tmp_path / "fixture.pdf"
    paper.write_bytes(b"%PDF-1.4 synthetic fixture; loader is injected")
    image = tmp_path / "fig.png"
    Image.new("RGB", (128, 128), "white").save(image)
    source = "Exact equation: alpha != beta.\n\nMeasured condition: 2.5 m/s.\n" * 30

    def respond(client, path, payload, identity, trace=None):
        if path == "/embeddings":
            assert len(payload["input"][0]) > len(source)
            assert "blue vortex" in payload["input"][0]
            return {
                "data": [
                    {"index": i, "embedding": [0.1] * 1024} for i in range(len(payload["input"]))
                ]
            }
        return {
            "id": "fixture",
            "usage": {"total_tokens": 12},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "A blue vortex; no readable legend."},
                }
            ],
        }

    monkeypatch.setattr(DashScopeClient, "post", respond)
    pipeline = IngestionPipeline(
        settings, collection="fixture-dashscope", runtime_root=tmp_path / "runtime"
    )
    pipeline.loader = SimpleNamespace(
        load=lambda _: Document(
            "d",
            source,
            {
                "source_path": str(paper),
                "paper_mode": True,
                "page_ranges": [{"page": 1, "start": 0, "end": len(source)}],
                "images": [{"id": "fig", "page": 1, "path": str(image)}],
            },
        )
    )
    try:
        result = pipeline.run(str(paper))
        assert result.success, result.error
        store = VectorStoreFactory.create(settings, collection_name="fixture-dashscope")
        records = store.collection.get(include=["documents", "metadatas"])
        assert records["documents"] == [source]
        assert image_annotations(records["metadatas"][0])[0]["description"].startswith(
            "A blue vortex"
        )
    finally:
        pipeline.close()
