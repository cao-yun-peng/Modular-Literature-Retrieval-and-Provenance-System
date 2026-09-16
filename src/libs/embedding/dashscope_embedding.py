"""DashScope embeddings with no character truncation and at most ten inputs per call."""

from __future__ import annotations

import math

from src.libs.dashscope_client import DashScopeClient, DashScopeError, sha
from src.libs.embedding.base_embedding import BaseEmbedding


class DashScopeEmbedding(BaseEmbedding):
    def __init__(self, settings, **kwargs):
        self.model = settings.embedding.model
        self.dimension = settings.embedding.dimensions
        self.client = DashScopeClient(settings, settings.embedding)

    def embed(self, texts, trace=None, **kwargs):
        self.validate_texts(texts)
        vectors = []
        for start in range(0, len(texts), 10):
            batch = texts[start : start + 10]
            response = self.client.post(
                "/embeddings",
                {
                    "model": self.model,
                    "input": batch,
                    "dimensions": self.dimension,
                    "encoding_format": "float",
                },
                {
                    "kind": "embedding",
                    "input_hashes": [sha(s) for s in batch],
                    "input_characters": [len(s) for s in batch],
                },
                trace,
            )
            rows = response.get("data", [])
            if sorted(row.get("index", -1) for row in rows) != list(range(len(batch))):
                raise DashScopeError("Embedding response has missing or duplicate input indices")
            for row in sorted(rows, key=lambda r: r["index"]):
                vector = row.get("embedding", [])
                if len(vector) != self.dimension or not all(
                    isinstance(x, (int, float)) and math.isfinite(x) for x in vector
                ):
                    raise DashScopeError("Embedding response has invalid dimensions or values")
                vectors.append(vector)
        return vectors

    def get_dimension(self):
        return self.dimension
