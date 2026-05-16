# References Chunk Demotion - Design Spec

**Date:** 2026-05-06
**Status:** Approved

## Goal

During paper ingestion, detect and mark reference/bibliography chunks with `chunk_type: "reference"` metadata. During retrieval, apply a configurable weight multiplier (default 0.3) to lower their ranking.

## Architecture

Three changes in three files:
1. `document_chunker.py` - Mark reference chunks during splitting
2. `hybrid_search.py` - Apply weight penalty post-fusion
3. `settings.yaml` + `settings.py` - Configurable `reference_weight`

## Data Flow

```
Paper PDF → PdfLoader (extracts references_raw) → DocumentChunker
  → GROBID path: references_raw → split → chunk_type="reference"
  → Legacy path: "# References" heading → split → chunk_type="reference"
  → VectorUpserter (stores chunk_type in metadata)
  → HybridSearch.search()
    → Dense + Sparse retrieval
    → RRF Fusion
    → Metadata Filter
    → ★ Reference weight penalty (score *= reference_weight)
    → Re-sort
    → Top-K
    → Reranker (optional)
```

## Changes

### 1. Settings (`src/core/settings.py`)
- Add `reference_weight: float` to `RetrievalSettings` dataclass
- Load via `_require_number(retrieval, "reference_weight", "retrieval")`

### 2. Config (`config/settings.yaml`)
- Add `reference_weight: 0.3` under `retrieval:` section

### 3. Chunker (`src/ingestion/chunking/document_chunker.py`)
- Legacy path: detect sections whose title matches `references|bibliography|参考文献` (case-insensitive), set `chunk_type: "reference"` on all child chunks
- GROBID path: check `metadata.get("references_raw")`, if present split it and append reference chunks with `chunk_type: "reference"`

### 4. HybridSearch (`src/core/query_engine/hybrid_search.py`)
- Add `reference_weight: float = 0.3` to `HybridSearchConfig`
- Extract from `settings.retrieval.reference_weight` in `_extract_config()`
- New method `_apply_reference_demotion(results, weight)` called after fusion, before top-k
- Check `result.metadata.get("chunk_type") == "reference"`, multiply score by weight, re-sort

## Non-Modifications
- `RRFFusion` - weight applied after fusion, not inside it
- `DenseRetriever` / `SparseRetriever` - unaware of chunk types
- `Reranker` - receives already-demoted ordering
- `Chunk` / `RetrievalResult` types - no schema changes needed
