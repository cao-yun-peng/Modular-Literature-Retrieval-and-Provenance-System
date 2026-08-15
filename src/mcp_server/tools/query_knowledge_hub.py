"""MCP Tool: query_knowledge_hub

This tool provides knowledge retrieval capabilities through the MCP protocol.
It combines HybridSearch (Dense + Sparse + RRF Fusion) with optional Reranking
to find relevant documents and return formatted results with citations.

Usage via MCP:
    Tool name: query_knowledge_hub
    Input schema:
        - query (string, required): The search query
        - top_k (integer, optional): Number of results to return (default: 5)
        - collection (string, optional): Limit search to specific collection
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from mcp import types

from src.core.response.response_builder import ResponseBuilder, MCPToolResponse
from src.core.settings import get_bm25_index_dir, load_settings, Settings
from src.core.trace import TraceContext, TraceCollector
from src.core.types import RetrievalResult

if TYPE_CHECKING:
    from src.core.query_engine.hybrid_search import HybridSearch
    from src.core.query_engine.reranker import CoreReranker

logger = logging.getLogger(__name__)


# Tool metadata
TOOL_NAME = "query_knowledge_hub"
TOOL_DESCRIPTION = """Search the knowledge base for relevant documents.

This tool uses hybrid search (semantic + keyword) to find the most relevant 
documents matching your query. Results include source citations for reference.

Parameters:
- query: Your search question or keywords
- top_k: Maximum number of results (default: 5)
- collection: Limit search to a specific document collection
- retrieval_mode: hybrid, section, or evidence
- document_ids / zotero_item_keys: Optional source scope
- expand_context: none, neighbors, parent, or adaptive
- allow_fulltext_handoff: Return an Agent-side Zotero fulltext recommendation
"""

TOOL_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query or question to find relevant documents for.",
        },
        "top_k": {
            "type": "integer",
            "description": "Maximum number of results to return.",
            "default": 5,
            "minimum": 1,
            "maximum": 20,
        },
        "collection": {
            "type": "string",
            "description": "Optional collection name to limit the search scope.",
        },
        "retrieval_mode": {
            "type": "string",
            "enum": ["hybrid", "section", "evidence"],
            "default": "hybrid",
        },
        "document_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "default": [],
        },
        "zotero_item_keys": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "default": [],
        },
        "expand_context": {
            "type": "string",
            "enum": ["none", "neighbors", "parent", "adaptive"],
        },
        "allow_fulltext_handoff": {
            "type": "boolean",
            "default": True,
        },
    },
    "required": ["query"],
}


@dataclass
class QueryKnowledgeHubConfig:
    """Configuration for query_knowledge_hub tool.
    
    Attributes:
        default_top_k: Default number of results if not specified
        max_top_k: Maximum allowed top_k value
        default_collection: Default collection if not specified
        enable_rerank: Whether to apply reranking
    """
    default_top_k: int = 5
    max_top_k: int = 20
    default_collection: str = "default"
    enable_rerank: bool = True


class QueryKnowledgeHubTool:
    """MCP Tool for knowledge base queries.
    
    This class encapsulates the query_knowledge_hub tool logic,
    coordinating HybridSearch and Reranker to produce formatted results.
    
    Design Principles:
    - Lazy initialization: Components created on first use
    - Error resilience: Graceful handling of search/rerank failures
    - Configurable: All parameters from settings.yaml
    
    Example:
        >>> tool = QueryKnowledgeHubTool(settings)
        >>> result = await tool.execute(query="Azure 配置", top_k=5)
        >>> print(result.content)
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        config: Optional[QueryKnowledgeHubConfig] = None,
        hybrid_search: Optional[HybridSearch] = None,
        reranker: Optional[CoreReranker] = None,
        response_builder: Optional[ResponseBuilder] = None,
    ) -> None:
        """Initialize QueryKnowledgeHubTool.

        Args:
            settings: Application settings. If None, loaded from default path.
            config: Tool configuration. If None, uses defaults.
            hybrid_search: Optional pre-configured HybridSearch instance.
            reranker: Optional pre-configured CoreReranker instance.
            response_builder: Optional pre-configured ResponseBuilder instance.
        """
        self._settings = settings
        self.config = config or QueryKnowledgeHubConfig()
        self._hybrid_search = hybrid_search
        self._reranker = reranker
        self._embedding_client = None
        self._response_builder = response_builder or ResponseBuilder()
        self._vector_store = None  # saved for linked-asset resolution
        self._section_store = None
        self._last_rerank_fallback = False

        # Track initialization state
        self._initialized = False
        self._current_collection: Optional[str] = None
    
    @property
    def settings(self) -> Settings:
        """Get settings, loading if necessary."""
        if self._settings is None:
            self._settings = load_settings()
        return self._settings
    
    def _ensure_initialized(self, collection: str) -> None:
        """Ensure search components are initialized for the given collection.
        
        Caching strategy (balances speed vs freshness):
        - **Fully cached** (stateless, never go stale): embedding client,
          reranker, query processor, settings.
        - **Cached until collection changes**: vector store (ChromaDB
          PersistentClient reads from SQLite — sees data written by other
          processes), dense retriever, hybrid search.
        - **Auto-refreshes on every query**: BM25 sparse index — the
          ``SparseRetriever._ensure_index_loaded()`` always reloads from
          disk, so the cached SparseRetriever object is fine.
        
        Only when *collection* changes do we tear down and rebuild.
        
        Args:
            collection: Target collection name.
        """
        # Fast path: already initialised for the same collection
        if self._initialized and self._current_collection == collection:
            logger.debug(
                "Query components already initialised for collection: %s",
                collection,
            )
            return
        
        logger.info(f"Initializing query components for collection: {collection}")
        
        # Import here to avoid circular imports and allow lazy loading
        from src.core.query_engine.query_processor import QueryProcessor
        from src.core.query_engine.hybrid_search import create_hybrid_search
        from src.core.query_engine.dense_retriever import create_dense_retriever
        from src.core.query_engine.sparse_retriever import create_sparse_retriever
        from src.core.query_engine.reranker import create_core_reranker
        from src.ingestion.storage.bm25_indexer import BM25Indexer
        from src.libs.embedding.embedding_factory import EmbeddingFactory
        from src.libs.vector_store.vector_store_factory import VectorStoreFactory
        
        # === Fully cached components (stateless, never go stale) ===
        if self._embedding_client is None:
            self._embedding_client = EmbeddingFactory.create(self.settings)
        
        if self._reranker is None:
            self._reranker = create_core_reranker(settings=self.settings)
        
        # === Rebuild for new collection ===
        # ChromaDB PersistentClient uses SQLite under the hood —
        # concurrent readers see committed writes from other processes
        # (dashboard ingestion), so caching the client is safe.
        vector_store = VectorStoreFactory.create(
            self.settings,
            collection_name=collection,
        )
        self._vector_store = vector_store  # save for linked-asset resolution
        hierarchy = (
            self.settings.ingestion.hierarchical_chunking
            if self.settings.ingestion
            else None
        )
        if hierarchy and hierarchy.enabled:
            from src.core.settings import resolve_path
            from src.ingestion.storage.section_store import SectionStore

            self._section_store = SectionStore(resolve_path(hierarchy.section_store_db))
        else:
            self._section_store = None

        dense_retriever = create_dense_retriever(
            settings=self.settings,
            embedding_client=self._embedding_client,
            vector_store=vector_store,
        )
        
        # BM25Indexer just holds the index dir path; the SparseRetriever
        # calls _ensure_index_loaded() on every search, which always
        # reloads from disk — so it picks up dashboard-written data.
        bm25_indexer = BM25Indexer(
            index_dir=str(get_bm25_index_dir(collection, self.settings))
        )
        sparse_retriever = create_sparse_retriever(
            settings=self.settings,
            bm25_indexer=bm25_indexer,
            vector_store=vector_store,
        )
        sparse_retriever.default_collection = collection
        
        query_processor = QueryProcessor()
        self._hybrid_search = create_hybrid_search(
            settings=self.settings,
            query_processor=query_processor,
            dense_retriever=dense_retriever,
            sparse_retriever=sparse_retriever,
        )
        
        self._current_collection = collection
        self._initialized = True
        logger.info(f"Query components initialized for collection: {collection}")
    
    async def execute(
        self,
        query: str,
        top_k: Optional[int] = None,
        collection: Optional[str] = None,
        retrieval_mode: str = "hybrid",
        document_ids: Optional[List[str]] = None,
        zotero_item_keys: Optional[List[str]] = None,
        expand_context: Optional[str] = None,
        allow_fulltext_handoff: bool = True,
    ) -> MCPToolResponse:
        """Execute the query_knowledge_hub tool.
        
        Args:
            query: Search query string.
            top_k: Maximum results to return.
            collection: Target collection name.
            retrieval_mode: ``hybrid``, ``section`` or ``evidence``.
            document_ids: Optional project document scope.
            zotero_item_keys: Optional Zotero item scope.
            expand_context: Parent/neighbor expansion policy.
            allow_fulltext_handoff: Whether an optional Agent action may be returned.
            
        Returns:
            MCPToolResponse with formatted content and citations.
            
        Raises:
            ValueError: If query is empty or invalid.
        """
        # Validate query
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        if retrieval_mode not in {"hybrid", "section", "evidence"}:
            raise ValueError("retrieval_mode must be hybrid, section, or evidence")
        document_ids = self._validate_scope_values("document_ids", document_ids)
        zotero_item_keys = self._validate_scope_values(
            "zotero_item_keys", zotero_item_keys
        )
        if not isinstance(allow_fulltext_handoff, bool):
            raise ValueError("allow_fulltext_handoff must be a boolean")
        
        # Apply defaults
        effective_top_k = min(
            top_k or self.config.default_top_k,
            self.config.max_top_k
        )
        effective_collection = collection or self.config.default_collection
        effective_expand_context = expand_context or self._default_expansion_mode(
            retrieval_mode
        )
        if effective_expand_context not in {"none", "neighbors", "parent", "adaptive"}:
            raise ValueError("expand_context is invalid")
        
        logger.info(
            f"Executing query_knowledge_hub: query='{query[:50]}...', "
            f"top_k={effective_top_k}, collection={effective_collection}"
        )
        
        trace = TraceContext(trace_type="query")
        trace.metadata["query"] = query[:200]
        trace.metadata["top_k"] = effective_top_k
        trace.metadata["collection"] = effective_collection
        trace.metadata["source"] = "mcp"
        trace.metadata["retrieval_mode"] = retrieval_mode
        trace.metadata["expand_context"] = effective_expand_context
        self._last_rerank_fallback = False

        try:
            # Initialize components for collection
            # Run blocking I/O (embedding API, ChromaDB, BM25) in a thread
            # to avoid blocking the async event loop / MCP stdio transport
            import time as _time
            _init_t0 = _time.monotonic()
            await asyncio.to_thread(self._ensure_initialized, effective_collection)
            _init_elapsed = (_time.monotonic() - _init_t0) * 1000.0
            trace.record_stage("initialization", {
                "collection": effective_collection,
                "cold_start": _init_elapsed > 500,  # >500ms ≈ cold
            }, elapsed_ms=_init_elapsed)
            selected_mode = retrieval_mode
            mode_fallback = False
            if retrieval_mode == "section" and self._section_store is None:
                selected_mode = "hybrid"
                mode_fallback = True
            
            # Perform hybrid search (blocking: embedding API + DB queries)
            candidate_k = min(
                max(
                    effective_top_k,
                    effective_top_k * self.settings.retrieval.candidate_multiplier,
                ),
                self.settings.retrieval.candidate_max,
            )
            results = await asyncio.to_thread(
                self._perform_search,
                query,
                candidate_k,
                trace,
                document_ids,
                zotero_item_keys,
            )
            trace.record_stage(
                "semantic_discovery",
                {
                    "requested_mode": retrieval_mode,
                    "selected_mode": selected_mode,
                    "candidate_count": len(results),
                    "fallback": mode_fallback,
                    "reason": (
                        "SectionStore is unavailable for this collection"
                        if mode_fallback
                        else "requested retrieval mode is available"
                    ),
                },
            )
            
            # Apply reranking if enabled (may call LLM API)
            if self.config.enable_rerank and results:
                results = await asyncio.to_thread(
                    self._apply_rerank, query, results, effective_top_k, trace,
                )

            if self.settings.evidence.deduplicate:
                from src.core.query_engine.evidence_deduplicator import (
                    EvidenceDeduplicator,
                )

                results = EvidenceDeduplicator().deduplicate(
                    results,
                    top_k=effective_top_k,
                    trace=trace,
                )
            else:
                results = results[:effective_top_k]

            from src.core.query_engine.context_expander import ContextExpander

            results = await asyncio.to_thread(
                ContextExpander(
                    self._section_store,
                    self._vector_store,
                    self.settings.evidence.max_context_characters,
                ).expand,
                results,
                mode=effective_expand_context,
                collection=effective_collection,
                trace=trace,
            )

            # Resolve linked figures/tables (blocking: ChromaDB query)
            linked_assets = await asyncio.to_thread(
                self._resolve_linked_assets, results,
            )

            # Build response
            response = self._response_builder.build(
                results=results,
                query=query,
                collection=effective_collection,
                linked_assets=linked_assets,
            )
            if not self.settings.evidence.include_zotero_identity:
                for citation in response.citations:
                    citation.metadata.pop("zotero_item_key", None)
                    citation.metadata.pop("zotero_attachment_key", None)

            from src.core.query_engine.fulltext_handoff_policy import (
                FulltextHandoffPolicy,
                HandoffDecision,
            )
            from src.core.response.evidence_bundle import EvidenceBundleBuilder

            decision = (
                FulltextHandoffPolicy(self.settings.agent_handoff).decide(query, results)
                if allow_fulltext_handoff
                and self.settings.evidence.include_zotero_identity
                else HandoffDecision(
                    signal="not_evaluated",
                    reason="fulltext handoff was disabled for this request",
                )
            )
            trace.record_stage(
                "fulltext_handoff_decision",
                {
                    "retrieval_mode": retrieval_mode,
                    "evidence_count": len(results),
                    "coverage_signal": decision.signal,
                    "reason": decision.reason,
                    "recommended_zotero_attachment_keys": [
                        document.get("zotero_attachment_key", "")
                        for document in decision.recommended_documents
                    ],
                    "project_did_not_fetch_fulltext": True,
                },
            )
            bundle = EvidenceBundleBuilder(
                include_score_breakdown=self.settings.evidence.include_score_breakdown,
                include_zotero_identity=self.settings.evidence.include_zotero_identity,
            ).build(
                query=query,
                collection=effective_collection,
                requested_mode=retrieval_mode,
                selected_mode=selected_mode,
                results=results,
                citations=response.citations,
                decision=decision,
                fallback=self._last_rerank_fallback or mode_fallback,
                candidate_count=candidate_k,
            )
            response.evidence_bundle = bundle
            response.metadata.update(
                {
                    "evidence_bundle_version": bundle["schema_version"],
                    "retrieval": bundle["retrieval"],
                    "coverage": bundle["coverage"],
                    "recommended_next_action": bundle["recommended_next_action"],
                }
            )
            trace.record_stage(
                "evidence_bundle_building",
                {
                    "schema_version": bundle["schema_version"],
                    "evidence_count": len(bundle["evidence"]),
                    "includes_zotero_identity": any(
                        evidence.get("zotero_attachment_key")
                        for evidence in bundle["evidence"]
                    ),
                },
            )
            
            # Store final results in trace for dashboard display
            trace.metadata["final_results"] = [
                {
                    "chunk_id": r.chunk_id,
                    "score": round(r.score, 4),
                    "text": (
                        r.text or ""
                        if self.settings.observability.include_content
                        else ""
                    ),
                    "source": r.metadata.get("source_path", r.metadata.get("source", "")),
                    "title": r.metadata.get("title", ""),
                }
                for r in results
            ]

            logger.info(
                f"query_knowledge_hub completed: {len(results)} results, "
                f"is_empty={response.is_empty}"
            )
            
            TraceCollector().collect(trace)
            return response
            
        except Exception as e:
            logger.exception(f"query_knowledge_hub failed: {e}")
            TraceCollector().collect(trace)
            # Return error response
            return self._build_error_response(query, effective_collection, str(e))
    
    def _perform_search(
        self,
        query: str,
        top_k: int,
        trace: Optional[Any] = None,
        document_ids: Optional[List[str]] = None,
        zotero_item_keys: Optional[List[str]] = None,
    ) -> List[RetrievalResult]:
        """Perform hybrid search.
        
        Args:
            query: Search query.
            top_k: Maximum results.
            trace: Optional TraceContext for observability.
            
        Returns:
            List of RetrievalResult.
        """
        if self._hybrid_search is None:
            raise RuntimeError("HybridSearch not initialized")
        
        try:
            results = self._hybrid_search.search(
                query=query,
                top_k=top_k,
                filters=None,
                trace=trace,
                return_details=False,
            )
            values = results if isinstance(results, list) else results.results
            return self._filter_result_scope(values, document_ids, zotero_item_keys)
        except Exception as e:
            logger.warning(f"Hybrid search failed: {e}")
            return []
    
    def _apply_rerank(
        self,
        query: str,
        results: List[RetrievalResult],
        top_k: int,
        trace: Optional[Any] = None,
    ) -> List[RetrievalResult]:
        """Apply reranking to search results.
        
        Args:
            query: Original query.
            results: Search results to rerank.
            top_k: Final number of results.
            trace: Optional TraceContext for observability.
            
        Returns:
            Reranked results (or original if reranking fails).
        """
        if self._reranker is None or not self._reranker.is_enabled:
            self._last_rerank_fallback = False
            return results[:top_k]
        
        try:
            conservative = self.settings.rerank.strategy == "conservative"
            rerank_result = self._reranker.rerank(
                query=query,
                results=results,
                top_k=len(results) if conservative else top_k,
                trace=trace,
            )
            self._last_rerank_fallback = rerank_result.used_fallback
            
            if rerank_result.used_fallback:
                logger.warning(
                    f"Reranker fallback: {rerank_result.fallback_reason}"
                )
            
            if conservative and not rerank_result.used_fallback:
                return self._conservative_fusion(
                    results,
                    rerank_result.results,
                    top_k,
                    self.settings.rerank.rrf_weight,
                )
            return rerank_result.results
        except Exception as e:
            logger.warning(f"Reranking failed, using original order: {e}")
            self._last_rerank_fallback = True
            return results[:top_k]

    @staticmethod
    def _conservative_fusion(
        original: List[RetrievalResult],
        reranked: List[RetrievalResult],
        top_k: int,
        rrf_weight: float,
    ) -> List[RetrievalResult]:
        def normalize(values: Dict[str, float]) -> Dict[str, float]:
            if not values:
                return {}
            low, high = min(values.values()), max(values.values())
            if high == low:
                return {key: 1.0 for key in values}
            return {key: (value - low) / (high - low) for key, value in values.items()}

        original_by_id = {result.chunk_id: result for result in original}
        rerank_by_id = {result.chunk_id: result.score for result in reranked}
        normalized_rrf = normalize(
            {result.chunk_id: float(result.score) for result in original}
        )
        normalized_rerank = normalize(rerank_by_id)
        fused: List[RetrievalResult] = []
        for chunk_id, source in original_by_id.items():
            final_score = (
                rrf_weight * normalized_rrf[chunk_id]
                + (1.0 - rrf_weight) * normalized_rerank.get(chunk_id, 0.0)
            )
            fused.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    score=final_score,
                    text=source.text,
                    metadata={
                        **source.metadata,
                        "original_score": source.score,
                        "rerank_score": rerank_by_id.get(chunk_id),
                        "final_score": final_score,
                        "rerank_strategy": "conservative",
                    },
                )
            )
        return sorted(fused, key=lambda result: (-result.score, result.chunk_id))[:top_k]

    @staticmethod
    def _validate_scope_values(
        name: str, values: Optional[List[str]]
    ) -> List[str]:
        if values is None:
            return []
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError(f"{name} must be a list with at most 50 values")
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        return list(dict.fromkeys(cleaned))

    def _default_expansion_mode(self, retrieval_mode: str) -> str:
        if retrieval_mode == "section":
            return "parent"
        if retrieval_mode == "evidence":
            return "none"
        return self.settings.evidence.expand_context

    @staticmethod
    def _filter_result_scope(
        results: List[RetrievalResult],
        document_ids: Optional[List[str]],
        zotero_item_keys: Optional[List[str]],
    ) -> List[RetrievalResult]:
        document_scope = set(document_ids or [])
        zotero_scope = set(zotero_item_keys or [])
        if not document_scope and not zotero_scope:
            return results
        filtered = []
        for result in results:
            metadata = result.metadata or {}
            document_id = str(
                metadata.get("document_id", metadata.get("source_ref", ""))
            )
            zotero_item_key = str(metadata.get("zotero_item_key", ""))
            if document_scope and document_id not in document_scope:
                continue
            if zotero_scope and zotero_item_key not in zotero_scope:
                continue
            filtered.append(result)
        return filtered
    
    def _resolve_linked_assets(
        self,
        results: List[RetrievalResult],
    ) -> Dict[str, Any]:
        """Resolve linked figure/table chunks for body chunks.

        For each result whose metadata carries ``linked_figures`` or
        ``linked_tables``, queries the vector store for the corresponding
        asset chunks and returns a mapping from ``chunk_id`` to asset data.

        Returns:
            Dict of ``{chunk_id: {"figures": [...], "tables": [...]}}``.
            Empty dict when no linked assets are found or vector store
            is unavailable.
        """
        if self._vector_store is None:
            return {}

        # Only ChromaStore supports get_by_metadata
        if not hasattr(self._vector_store, "get_by_metadata"):
            return {}

        linked: Dict[str, Any] = {}

        for result in results:
            metadata = result.metadata or {}
            linked_figs_raw = metadata.get("linked_figures", [])
            linked_tabs_raw = metadata.get("linked_tables", [])
            source_ref = metadata.get("source_ref", "")

            # ChromaDB sanitizes lists to comma-separated strings
            linked_figs = self._parse_metadata_list(linked_figs_raw)
            linked_tabs = self._parse_metadata_list(linked_tabs_raw)

            if not linked_figs and not linked_tabs:
                continue

            assets: Dict[str, list] = {"figures": [], "tables": []}

            for fig_id in linked_figs:
                filters: Dict[str, str] = {"figure_id": fig_id}
                if source_ref:
                    filters["source_ref"] = source_ref
                try:
                    chunks = self._vector_store.get_by_metadata(filters)
                    for chunk in chunks:
                        if chunk and chunk.get("text"):
                            fig_meta = chunk.get("metadata", {})
                            assets["figures"].append({
                                "figure_id": fig_id,
                                "caption": fig_meta.get("figure_caption", ""),
                                "text": chunk["text"],
                            })
                except Exception:
                    logger.debug("Failed to resolve figure %s", fig_id, exc_info=True)

            for tab_id in linked_tabs:
                filters = {"table_id": tab_id}
                if source_ref:
                    filters["source_ref"] = source_ref
                try:
                    chunks = self._vector_store.get_by_metadata(filters)
                    for chunk in chunks:
                        if chunk and chunk.get("text"):
                            tab_meta = chunk.get("metadata", {})
                            assets["tables"].append({
                                "table_id": tab_id,
                                "caption": tab_meta.get("table_caption", ""),
                                "text": chunk["text"],
                            })
                except Exception:
                    logger.debug("Failed to resolve table %s", tab_id, exc_info=True)

            if assets["figures"] or assets["tables"]:
                linked[result.chunk_id] = assets

        return linked

    @staticmethod
    def _parse_metadata_list(value: Any) -> List[str]:
        """Parse a metadata field that may be a list or comma-separated string.

        ChromaDB's ``_sanitize_metadata`` converts Python lists to
        comma-joined strings, so stored ``linked_figures`` values come
        back as ``"fig_0,fig_1"`` instead of ``["fig_0", "fig_1"]``.
        """
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [v.strip() for v in value.split(",") if v.strip()]
        return []

    def _build_error_response(
        self,
        query: str,
        collection: str,
        error_message: str,
    ) -> MCPToolResponse:
        """Build error response.
        
        Args:
            query: Original query.
            collection: Target collection.
            error_message: Error description.
            
        Returns:
            MCPToolResponse indicating error.
        """
        content = f"## 查询失败\n\n"
        content += f"查询: **{query}**\n"
        content += f"集合: `{collection}`\n\n"
        content += f"**错误信息:** {error_message}\n\n"
        content += "请检查:\n"
        content += "- 数据库连接是否正常\n"
        content += "- 集合是否已创建并包含数据\n"
        content += "- 配置文件是否正确\n"
        
        return MCPToolResponse(
            content=content,
            citations=[],
            metadata={
                "query": query,
                "collection": collection,
                "error": error_message,
            },
            is_empty=True,
        )


# Module-level tool instance (lazy-initialized)
_tool_instance: Optional[QueryKnowledgeHubTool] = None


def get_tool_instance(settings: Optional[Settings] = None) -> QueryKnowledgeHubTool:
    """Get or create the tool instance.
    
    Args:
        settings: Optional settings to use for initialization.
        
    Returns:
        QueryKnowledgeHubTool instance.
    """
    global _tool_instance
    if _tool_instance is None:
        _tool_instance = QueryKnowledgeHubTool(settings=settings)
    return _tool_instance


async def query_knowledge_hub_handler(
    query: str,
    top_k: int = 5,
    collection: Optional[str] = None,
    retrieval_mode: str = "hybrid",
    document_ids: Optional[List[str]] = None,
    zotero_item_keys: Optional[List[str]] = None,
    expand_context: Optional[str] = None,
    allow_fulltext_handoff: bool = True,
) -> types.CallToolResult:
    """Handler function for MCP tool registration.
    
    This function is registered with the ProtocolHandler and called
    when the MCP client invokes the query_knowledge_hub tool.
    
    Supports multimodal responses - if search results contain images,
    the response will include ImageContent blocks alongside TextContent.
    
    Args:
        query: Search query string.
        top_k: Maximum number of results.
        collection: Optional collection name.
        retrieval_mode: hybrid, section, or evidence.
        document_ids: Optional project document scope.
        zotero_item_keys: Optional Zotero item scope.
        expand_context: Optional hierarchy expansion mode.
        allow_fulltext_handoff: Return an optional Zotero Agent action.
        
    Returns:
        MCP CallToolResult with content blocks (text and optionally images).
    """
    tool = get_tool_instance()
    
    try:
        response = await tool.execute(
            query=query,
            top_k=top_k,
            collection=collection,
            retrieval_mode=retrieval_mode,
            document_ids=document_ids,
            zotero_item_keys=zotero_item_keys,
            expand_context=expand_context,
            allow_fulltext_handoff=allow_fulltext_handoff,
        )
        
        # Use to_mcp_content() which handles multimodal (text + images)
        content_blocks = response.to_mcp_content()
        
        return types.CallToolResult(
            content=content_blocks,
            isError=response.is_empty and "error" in response.metadata,
        )
        
    except ValueError as e:
        # Invalid parameters
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"参数错误: {e}",
                )
            ],
            isError=True,
        )
    except Exception as e:
        # Internal error
        logger.exception(f"query_knowledge_hub handler error: {e}")
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"内部错误: 查询处理失败",
                )
            ],
            isError=True,
        )


def register_tool(protocol_handler) -> None:
    """Register query_knowledge_hub tool with the protocol handler.
    
    Args:
        protocol_handler: ProtocolHandler instance to register with.
    """
    protocol_handler.register_tool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        input_schema=TOOL_INPUT_SCHEMA,
        handler=query_knowledge_hub_handler,
    )
    logger.info(f"Registered MCP tool: {TOOL_NAME}")
