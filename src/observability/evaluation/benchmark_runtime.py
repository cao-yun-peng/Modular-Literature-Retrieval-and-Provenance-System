"""Real project pipeline adapters and isolated, resumable benchmark indexing."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import platform
import sqlite3
import struct
import subprocess
from dataclasses import asdict, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .benchmark_authoring import load_local_key
from .benchmark_data import SCHEMA, digest, index_identity, now, read_json, write_json


def sanitized(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if any(s in k.lower() for s in ("api_key", "secret", "password", "authorization"))
            else sanitized(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitized(v) for v in value]
    return str(value) if isinstance(value, Path) else value


def environment_manifest(settings) -> dict:
    def git(*args):
        p = subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        return p.stdout.strip() if p.returncode == 0 else None

    packages = {}
    for name in (
        "chromadb",
        "PyMuPDF",
        "langchain-text-splitters",
        "sentence-transformers",
        "torch",
        "tiktoken",
        "Pillow",
        "httpx",
    ):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "created_at": now(),
        "settings": sanitized(asdict(settings)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "machine": platform.machine(),
        "packages": packages,
        "git_commit": git("rev-parse", "HEAD"),
        "working_diff_sha256": digest(git("diff", "--", "src", "scripts") or ""),
        "benchmark_source_sha256": digest(
            {p.name: digest(p.read_bytes()) for p in Path(__file__).parent.glob("benchmark_*.py")}
        ),
        "embedding_runtime_identity": ollama_identity(settings),
    }


def ollama_identity(settings) -> dict | None:
    if settings.embedding.provider != "ollama":
        return None
    import urllib.request

    try:
        url = (settings.embedding.base_url or "http://localhost:11434").rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as response:
            models = json.load(response)["models"]
        return next(
            (
                m
                for m in models
                if m["name"].split(":")[0] == settings.embedding.model.split(":")[0]
            ),
            None,
        )
    except Exception:
        return {"status": "unavailable"}


def isolated_settings(root: Path, config: str | None = None, *, hierarchy: bool | None = None):
    from src.core.settings import load_settings

    load_local_key()
    settings = load_settings(config)
    runtime = (root / "runtime").resolve()
    ingestion = settings.ingestion
    if ingestion is None:
        raise ValueError("Benchmark requires ingestion settings")
    if hierarchy is None:
        hierarchy = ingestion.splitter != "token"
    # Explicit evaluation profile: source-preserving transforms, current embedding.
    ingestion = replace(
        ingestion,
        hierarchical_chunking=replace(
            ingestion.hierarchical_chunking,
            enabled=hierarchy,
            section_store_db=str(runtime / "db/section_store.sqlite3"),
        ),
    )
    # Ingestion transform settings are dictionaries in the existing schema.
    ingestion = replace(
        ingestion, chunk_refiner={"use_llm": False}, metadata_enricher={"use_llm": False}
    )
    return replace(
        settings,
        ingestion=ingestion,
        vector_store=replace(settings.vector_store, persist_directory=str(runtime / "chroma")),
        observability=replace(settings.observability, trace_file=str(runtime / "traces.jsonl")),
    )


def snapshot_index(settings, corpus: dict, collection: str) -> dict:
    from src.core.settings import get_bm25_index_dir
    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

    store = VectorStoreFactory.create(settings, collection_name=collection)
    data = store.collection.get(include=["documents", "metadatas"])
    papers_by_filename = {
        Path(p["pdf_file"]).name: p["paper_id"] for p in corpus["papers"] if p.get("pdf_file")
    }
    chunks = []
    for cid, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        meta = meta or {}
        pid = meta.get("benchmark_paper_id") or papers_by_filename.get(
            Path(meta.get("source_path", "")).name
        )
        chunks.append({"chunk_id": cid, "paper_id": pid, "text": text or "", "metadata": meta})
    chunks.sort(key=lambda c: c["chunk_id"])
    vectors = hashlib.sha256()
    for start in range(0, len(chunks), 256):
        batch = store.collection.get(
            ids=[c["chunk_id"] for c in chunks[start : start + 256]], include=["embeddings"]
        )
        for cid, vector in sorted(zip(batch["ids"], batch["embeddings"]), key=lambda pair: pair[0]):
            vectors.update(cid.encode("utf-8"))
            vectors.update(struct.pack("<" + "f" * len(vector), *vector))
    sparse_path = get_bm25_index_dir(collection, settings) / f"{collection}_bm25.json"
    storage = {
        "vectors_sha256": vectors.hexdigest(),
        "bm25_sha256": digest(sparse_path.read_bytes()) if sparse_path.exists() else None,
    }
    sections = Path(settings.ingestion.hierarchical_chunking.section_store_db)
    hierarchy = {}
    if sections.exists():
        connection = sqlite3.connect(sections.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            for table, key in (("sections", "parent_id"), ("section_children", "child_id")):
                hierarchy[table] = [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE collection=? ORDER BY {key}", (collection,)
                    )
                ]
        finally:
            connection.close()
    storage["hierarchy_sha256"] = digest(hierarchy) if hierarchy else None
    return {
        "schema_version": SCHEMA,
        "corpus_id": corpus["corpus_id"],
        "collection": collection,
        "index_id": index_identity(chunks),
        "storage_id": digest(storage),
        "storage": storage,
        "chunks": chunks,
        "environment": environment_manifest(settings),
    }


def ingest_corpus(root: Path, corpus: dict, settings, collection: str) -> dict:
    from src.core.types import Chunk
    from src.ingestion.embedding.sparse_encoder import SparseEncoder
    from src.ingestion.pipeline import IngestionPipeline

    record_path = root / "ingestion.json"
    previous = read_json(record_path) if record_path.exists() else {"documents": {}}
    config_id = digest(sanitized(asdict(settings)))
    if previous.get("config_id", config_id) != config_id:
        raise ValueError("Ingestion configuration changed; create a new benchmark version")
    record = {
        "corpus_id": corpus["corpus_id"],
        "config_id": config_id,
        "profile": {"splitter": settings.ingestion.splitter,
                    "hierarchy": settings.ingestion.hierarchical_chunking.enabled,
                    "vision": settings.vision_llm.enabled if settings.vision_llm else False},
        "documents": previous["documents"],
        "environment": environment_manifest(settings),
    }
    pipeline = IngestionPipeline(
        settings, collection=collection, use_paper_loader=True, runtime_root=root / "runtime"
    )
    if settings.embedding.provider == "ollama":
        pipeline.dense_encoder.embedding = BatchOllama(settings)
    try:
        for i, paper in enumerate(corpus["papers"]):
            pid = paper["paper_id"]
            if record["documents"].get(pid, {}).get("success"):
                continue
            print(f"INGEST [{i + 1}/{len(corpus['papers'])}] {paper['title']}", flush=True)
            if paper["status"] != "parsed":
                record["documents"][pid] = {
                    "success": False,
                    "error": paper.get("error", "source parse failed"),
                }
            else:
                result = pipeline.run(
                    str(root / paper["pdf_file"]),
                    source_metadata={
                        "benchmark_paper_id": pid,
                        "benchmark_family_id": paper["family_id"],
                        "source_type": "manual",
                    },
                )
                record["documents"][pid] = result.to_dict()
            write_json(record_path, record)
        index = snapshot_index(settings, corpus, collection)
        # Final idempotent merge from ALL indexed chunks verifies that both
        # retrievers have been offered the identical final corpus.
        chunks = [
            Chunk(id=c["chunk_id"], text=c["text"], metadata=c["metadata"]) for c in index["chunks"]
        ]
        if chunks:
            stats = SparseEncoder().encode(chunks)
            pipeline.bm25_indexer.build(stats, collection=collection)
            index = snapshot_index(settings, corpus, collection)
        index["ingestion"] = record["documents"]
        write_json(root / "index.json", index)
        return index
    finally:
        pipeline.close()


class BatchOllama:
    """Same model and cosine geometry; bounded batches avoid per-chunk connections."""

    def __init__(self, settings):
        self.settings = settings
        from src.libs.embedding.ollama_embedding import OllamaEmbedding

        self.max_input_chars = OllamaEmbedding(settings).max_input_chars

    def embed(self, texts, trace=None):
        import httpx

        texts = [text[: self.max_input_chars] for text in texts]

        url = (self.settings.embedding.base_url or "http://localhost:11434").rstrip(
            "/"
        ) + "/api/embed"
        result = []
        with httpx.Client(timeout=120, trust_env=False) as client:
            for start in range(0, len(texts), 16):
                response = client.post(
                    url,
                    json={
                        "model": self.settings.embedding.model,
                        "input": texts[start : start + 16],
                        "truncate": False,
                    },
                )
                response.raise_for_status()
                batch = response.json()["embeddings"]
                if len(batch) != len(texts[start : start + 16]):
                    raise ValueError("Embedding batch length mismatch")
                result.extend(batch)
        return result

    def get_dimension(self):
        return self.settings.embedding.dimensions


def pack(result, lookup: dict) -> dict:
    from src.core.retrieval_text import image_annotations
    row = lookup.get(result.chunk_id, {})
    meta = result.metadata or {}
    return {
        "chunk_id": result.chunk_id,
        "paper_id": row.get("paper_id"),
        "text": result.text or "",
        "score": result.score,
        "metadata": meta,
        "page_start": meta.get("page_start", meta.get("page", meta.get("page_num"))),
        "page_end": meta.get(
            "page_end", meta.get("page_start", meta.get("page", meta.get("page_num")))
        ),
        "expanded_context": meta.get("expanded_context"),
        "image_annotations": image_annotations(meta),
    }


class BenchmarkAdapter:
    def __init__(self, settings, index: dict):
        from src.core.query_engine.dense_retriever import create_dense_retriever
        from src.core.query_engine.hybrid_search import create_hybrid_search
        from src.core.query_engine.query_processor import QueryProcessor
        from src.core.query_engine.sparse_retriever import create_sparse_retriever
        from src.core.settings import get_bm25_index_dir
        from src.ingestion.storage.bm25_indexer import BM25Indexer
        from src.libs.embedding.embedding_factory import EmbeddingFactory
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory

        self.settings, self.index = settings, index
        self.collection = index["collection"]
        self.lookup = {c["chunk_id"]: c for c in index["chunks"]}
        self.store = VectorStoreFactory.create(settings, collection_name=self.collection)
        self.embedding = EmbeddingFactory.create(settings)
        self.dense = create_dense_retriever(
            settings=settings, embedding_client=self.embedding, vector_store=self.store
        )
        bm25 = BM25Indexer(index_dir=str(get_bm25_index_dir(self.collection, settings)))
        if not bm25.load(self.collection):
            raise ValueError("BM25 index missing (not an empty retrieval)")
        self.sparse = create_sparse_retriever(
            settings=settings, bm25_indexer=bm25, vector_store=self.store
        )
        self.sparse.default_collection = self.collection
        self.processor = QueryProcessor()
        self.hybrid = create_hybrid_search(
            settings=settings,
            query_processor=self.processor,
            dense_retriever=self.dense,
            sparse_retriever=self.sparse,
        )
        self.tools, self.rerankers = {}, {}

    def query(self, case: dict, k: int, strategy: str) -> dict:
        from src.core.trace import TraceContext

        trace = TraceContext(trace_type="query")
        params = dict(case.get("query_params", {}))
        if params.get("collection", self.collection) != self.collection:
            raise ValueError("Case targets another collection")
        params.pop("collection", None)
        if case.get("scope_paper_ids"):
            document_ids = {
                c["metadata"].get("document_id", c["metadata"].get("source_ref"))
                for c in self.index["chunks"]
                if c.get("paper_id") in case["scope_paper_ids"]
            }
            document_ids.discard(None)
            if not document_ids:
                raise ValueError("Scoped source papers are absent from the index")
            params["document_ids"] = sorted(document_ids)
        query = case["query"]
        if strategy == "service":
            return self.service(case, k, params)
        scoped_ids = params.get("document_ids", [])
        if strategy == "bm25":
            keywords = self.processor.process(query).keywords
            results = self.sparse.retrieve(
                keywords, top_k=20, collection=self.collection, trace=trace
            )
            fallback = False
        elif strategy == "dense":
            results = self.dense.retrieve(query, top_k=20, trace=trace)
            fallback = False
        else:
            found = self.hybrid.search(
                query=query,
                top_k=20,
                filters={"collection": self.collection},
                trace=trace,
                return_details=True,
            )
            results, fallback = found.results, found.used_fallback
        if scoped_ids:
            results = [
                r
                for r in results
                if r.metadata.get("document_id", r.metadata.get("source_ref")) in scoped_ids
            ]
        candidate_snapshot = [pack(r, self.lookup) for r in results]
        base_ms = trace.elapsed_ms()
        if strategy in ("cross_encoder", "llm"):
            results = self.rerank(query, results, strategy, trace)
        if strategy in ("expand_none", "expand_neighbors", "expand_parent"):
            from src.core.query_engine.context_expander import ContextExpander
            from src.ingestion.storage.section_store import SectionStore

            sections = SectionStore(self.settings.ingestion.hierarchical_chunking.section_store_db)
            results = ContextExpander(
                sections, self.store, self.settings.evidence.max_context_characters
            ).expand(
                results[:k],
                mode=strategy.removeprefix("expand_"),
                collection=self.collection,
                trace=trace,
            )
        trace.finish()
        return {
            "evidence": [pack(r, self.lookup) for r in results[:k]],
            "candidates": candidate_snapshot,
            "fallback": fallback,
            "trace": trace.to_dict(),
            "retrieval_ms": base_ms,
            "coverage": {"signal": "not_evaluated"},
            "recommended_next_action": None,
        }

    def rerank(self, query, results, strategy, trace):
        from src.core.query_engine.reranker import create_core_reranker

        if strategy not in self.rerankers:
            cfg = replace(
                self.settings, rerank=replace(self.settings.rerank, enabled=True, provider=strategy)
            )
            self.rerankers[strategy] = create_core_reranker(cfg)
        ranked = self.rerankers[strategy].rerank(
            query, copy.deepcopy(results), top_k=len(results), trace=trace
        )
        if ranked.used_fallback:
            raise RuntimeError(f"{strategy} fell back: {ranked.fallback_reason}")
        if sorted(r.chunk_id for r in results) != sorted(r.chunk_id for r in ranked.results):
            raise RuntimeError("Reranker changed the candidate set")
        return ranked.results

    def service(self, case, k, params):
        from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

        lookup = self.lookup

        class ObservedTool(QueryKnowledgeHubTool):
            def _perform_search(
                self, query, top_k, trace=None, document_ids=None, zotero_item_keys=None
            ):  # noqa: N805
                self.observed_trace = trace
                result = super()._perform_search(
                    query, top_k, trace, document_ids, zotero_item_keys
                )
                self.observed_candidates = [pack(r, lookup) for r in result]
                return result

        if "service" not in self.tools:
            self.tools["service"] = ObservedTool(settings=self.settings)
        tool = self.tools["service"]
        tool.observed_trace, tool.observed_candidates = None, []
        response = asyncio.run(
            tool.execute(query=case["query"], top_k=k, collection=self.collection, **params)
        )
        if response.metadata.get("error") or response.evidence_bundle is None:
            raise RuntimeError(str(response.metadata.get("error", "Missing Evidence Bundle")))
        bundle = response.evidence_bundle
        trace_data = tool.observed_trace.to_dict() if tool.observed_trace else {}
        stages = trace_data.get("stages", [])
        retrieval_fallback = any(
            s["data"].get("fallback") for s in stages if s["stage"] == "hybrid_retrieval_status"
        )
        errors = [s["data"]["error"] for s in stages if s["stage"] == "retrieval_error"]
        evidence = []
        for item in bundle["evidence"]:
            row = dict(item)
            row["paper_id"] = self.lookup.get(row["chunk_id"], {}).get("paper_id")
            evidence.append(row)
        return {
            **bundle,
            "evidence": evidence,
            "fallback": bundle["retrieval"].get("fallback", False) or retrieval_fallback,
            "observation_errors": errors,
            "raw_response": response.to_dict(),
            "candidates": tool.observed_candidates,
            "trace": trace_data,
        }

    def from_pool(self, case, pool, strategy, top_k=20):
        """Same-pool ablations never perform another search."""
        from src.core.trace import TraceContext
        from src.core.types import RetrievalResult

        trace = TraceContext(trace_type="query")
        if pool.get("fallback"):
            raise RuntimeError("Shared candidate retrieval degraded")
        original = pool["evidence"]
        results = [
            RetrievalResult(
                chunk_id=r["chunk_id"],
                score=r["score"],
                text=r["text"],
                metadata=copy.deepcopy(r.get("metadata", {})),
            )
            for r in original
        ]
        if strategy in ("llm", "cross_encoder"):
            results = self.rerank(case["query"], results, strategy, trace)
        if strategy.startswith("expand_"):
            from src.core.query_engine.context_expander import ContextExpander
            from src.ingestion.storage.section_store import SectionStore

            sections = SectionStore(self.settings.ingestion.hierarchical_chunking.section_store_db)
            results = ContextExpander(
                sections, self.store, self.settings.evidence.max_context_characters
            ).expand(
                results[:top_k],
                mode=strategy.removeprefix("expand_"),
                collection=self.collection,
                trace=trace,
            )
        trace.finish()
        return {
            "evidence": [pack(r, self.lookup) for r in results],
            "candidates": original,
            "pool_id": digest(original),
            "fallback": False,
            "trace": trace.to_dict(),
            "coverage": {"signal": "not_evaluated"},
            "pool_retrieval_ms": pool.get("retrieval_ms"),
        }
