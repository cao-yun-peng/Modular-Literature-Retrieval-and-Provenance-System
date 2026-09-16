#!/usr/bin/env python
"""Preview token chunks offline, then explicitly probe configured DashScope models."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.observability.evaluation.benchmark_data import read_json, write_json
from src.observability.evaluation.benchmark_runtime import isolated_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preview", "probe"])
    parser.add_argument("--root", type=Path, default=Path("data/paper_benchmark/v2-qwen2500"))
    parser.add_argument("--config", default="config/settings.dashscope.yaml")
    args = parser.parse_args()
    root = args.root.resolve()
    settings = isolated_settings(root, args.config)
    if args.mode == "preview":
        from src.ingestion.chunking.document_chunker import DocumentChunker
        from src.libs.loader.page_aware_pdf_loader import PageAwarePdfLoader

        corpus = read_json(root / "corpus.json")
        loader = PageAwarePdfLoader(
            image_storage_dir=root / "runtime/images" / ("paper-benchmark-" + root.name)
        )
        chunker = DocumentChunker(settings)
        rows, samples = [], []
        for i, paper in enumerate(corpus["papers"]):
            doc = loader.load(root / paper["pdf_file"])
            chunks = chunker.split_document(doc)
            images = doc.metadata.get("images", [])
            rows.append(
                {
                    "paper_id": paper["paper_id"],
                    "split": paper["split"],
                    "chunks": len(chunks),
                    "max_chunk_tokens": max(c.metadata["token_count"] for c in chunks),
                    "paper_tokens": chunker._splitter.count(doc.text),
                    "image_count": len(images),
                    "source_characters": len(doc.text),
                }
            )
            suitable = [
                img
                for img in images
                if min(
                    img.get("position", {}).get("width", 0),
                    img.get("position", {}).get("height", 0),
                )
                >= 160
            ]
            if paper["split"] == "dev" and suitable and len(samples) < 2:
                samples.append(
                    {
                        "paper_id": paper["paper_id"],
                        "title": paper["title"],
                        "image": suitable[0],
                        "text": chunks[0].text,
                    }
                )
            print(
                f"PREVIEW {i + 1}/{len(corpus['papers'])}: {len(chunks)} chunks, {len(images)} images",
                flush=True,
            )
        result = {
            "tokenizer": settings.ingestion.tokenizer,
            "tokenizer_role": "declared local splitting proxy, not the proprietary provider tokenizer",
            "chunk_size": settings.ingestion.chunk_size,
            "overlap": settings.ingestion.chunk_overlap,
            "papers": rows,
            "samples": samples,
        }
        write_json(root / "preview.json", result)
        print(
            json.dumps(
                {
                    "papers": len(rows),
                    "chunks": sum(r["chunks"] for r in rows),
                    "median_chunks": statistics.median(r["chunks"] for r in rows),
                    "max_chunk_tokens": max(r["max_chunk_tokens"] for r in rows),
                    "images": sum(r["image_count"] for r in rows),
                    "sample_images": [s["image"]["path"] for s in samples],
                },
                ensure_ascii=True,
            )
        )
    else:
        from src.libs.embedding.dashscope_embedding import DashScopeEmbedding
        from src.libs.llm.base_vision_llm import ImageInput
        from src.libs.llm.dashscope_vision_llm import DashScopeVisionLLM
        from src.libs.splitter.token_splitter import get_tokenizer

        samples = read_json(root / "preview.json")["samples"]
        embedding, vision = DashScopeEmbedding(settings), DashScopeVisionLLM(settings)
        prompt = Path(settings.vision_llm.prompt_path).read_text(encoding="utf-8")
        output = []
        for sample in samples:
            vector = embedding.embed([sample["text"]])[0]
            response = vision.chat_with_image(prompt, ImageInput(path=sample["image"]["path"]))
            output.append(
                {
                    "paper_id": sample["paper_id"],
                    "image": sample["image"],
                    "local_chunk_tokens": len(
                        get_tokenizer().encode(sample["text"], disallowed_special=())
                    ),
                    "embedding_dimensions": len(vector),
                    "description": response.content,
                    "vision_model": response.model,
                    "usage": response.usage,
                    "provenance": response.raw_response,
                }
            )
            print(
                json.dumps(
                    {
                        "paper_id": sample["paper_id"],
                        "dimensions": len(vector),
                        "caption_characters": len(response.content),
                        "vision_usage": response.usage,
                    }
                ),
                flush=True,
            )
        write_json(root / "preflight.json", {"success": len(output) == 2, "samples": output})


if __name__ == "__main__":
    main()
