# Reference Chunk Demotion - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark reference/bibliography chunks during paper chunking, and apply a 0.3x score penalty during hybrid search to demote them in rankings.

**Architecture:** Tag reference chunks with `chunk_type: "reference"` metadata at chunk time, then apply a configurable `reference_weight` multiplier in `HybridSearch.search()` after fusion but before top-K truncation.

**Tech Stack:** Python 3.13, dataclasses, pytest, unittest.mock

---

### Task 1: Add `reference_weight` to Settings

**Files:**
- Modify: `src/core/settings.py:127-131` (RetrievalSettings dataclass)
- Modify: `src/core/settings.py:259-264` (Settings.from_dict factory)
- Modify: `config/settings.yaml:67-70` (retrieval section)

- [ ] **Step 1: Add field to RetrievalSettings dataclass**

```python
# src/core/settings.py, replace lines 127-131
@dataclass(frozen=True)
class RetrievalSettings:
    dense_top_k: int
    sparse_top_k: int
    fusion_top_k: int
    rrf_k: int
    reference_weight: float
```

- [ ] **Step 2: Add loading logic in Settings.from_dict**

```python
# src/core/settings.py, replace lines 259-264
            retrieval=RetrievalSettings(
                dense_top_k=_require_int(retrieval, "dense_top_k", "retrieval"),
                sparse_top_k=_require_int(retrieval, "sparse_top_k", "retrieval"),
                fusion_top_k=_require_int(retrieval, "fusion_top_k", "retrieval"),
                rrf_k=_require_int(retrieval, "rrf_k", "retrieval"),
                reference_weight=_require_number(retrieval, "reference_weight", "retrieval"),
            ),
```

- [ ] **Step 3: Add to settings.yaml**

```yaml
# config/settings.yaml, add under retrieval: section (after rrf_k line)
  reference_weight: 0.3  # 参考文献 chunk 召回降权系数 (0=排除, 1=不降权)
```

- [ ] **Step 4: Run settings-related tests**

```bash
python -m pytest tests/unit/test_config_loading.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/core/settings.py config/settings.yaml
git commit -m "feat: add reference_weight to RetrievalSettings"
```

---

### Task 2: Mark reference chunks in DocumentChunker

**Files:**
- Modify: `src/ingestion/chunking/document_chunker.py:344-362` (_split_paper_document_legacy)
- Modify: `src/ingestion/chunking/document_chunker.py:236-258` (_split_paper_document_grobid)
- Modify: `tests/unit/test_document_chunker_paper.py` (add reference chunk tests)

- [ ] **Step 1: Write failing test for legacy reference detection**

```python
# tests/unit/test_document_chunker_paper.py, add after existing tests

def test_legacy_references_section_gets_reference_chunk_type():
    """Legacy path: chunks from # References section must have chunk_type='reference'."""
    from src.ingestion.chunking.document_chunker import DocumentChunker
    from src.core.types import Document

    settings = Mock()
    settings.ingestion = Mock()
    settings.ingestion.chunk_size = 500
    settings.ingestion.chunk_overlap = 50

    chunker = DocumentChunker(settings)
    text = (
        "# Introduction\n\nThis is the introduction text with some content.\n\n"
        "# Methods\n\nWe used standard methods for the experiment.\n\n"
        "# References\n\n[1] Smith, J. et al. A great paper. Nature, 2020.\n"
        "[2] Doe, A. et al. Another paper. Science, 2021.\n"
    )
    doc = Document(id="test_doc", text=text, metadata={})
    chunks = chunker.split_document(doc)

    ref_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "reference"]
    assert len(ref_chunks) >= 1, "References section should produce reference-type chunks"
    assert all("Smith" in c.text or "Doe" in c.text or "[1]" in c.text or "[2]" in c.text
               for c in ref_chunks)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/test_document_chunker_paper.py::test_legacy_references_section_gets_reference_chunk_type -v
```
Expected: FAIL

- [ ] **Step 3: Add reference section detection constant**

```python
# src/ingestion/chunking/document_chunker.py, add after DEFAULT_MAX_WORKERS (if not already present)
import re

_REFERENCE_SECTION_PATTERN = re.compile(
    r"^(references|bibliography|参考文献|引用文献|参考)\s*$", re.IGNORECASE
)
```

- [ ] **Step 4: Modify `_split_paper_document_legacy` to detect and mark reference sections**

Replace `_split_paper_document_legacy` (lines 344-362):

```python
def _split_paper_document_legacy(self, document: Document) -> List[Chunk]:
    sections = self._split_into_sections(document.text)
    chunks: List[Chunk] = []
    index = 0

    for section_title, section_text in sections:
        is_reference = bool(_REFERENCE_SECTION_PATTERN.match(section_title.strip()))
        text_fragments = self._splitter.split_text(section_text)
        for text in text_fragments:
            chunk_id = self._generate_chunk_id(document.id, index, text)
            chunk_metadata = self._inherit_metadata(document, index, text)
            if section_title:
                chunk_metadata["section"] = section_title
            if is_reference:
                chunk_metadata["chunk_type"] = "reference"

            self._enrich_paper_chunk_metadata(chunk_metadata, text)
            chunk = Chunk(id=chunk_id, text=text, metadata=chunk_metadata)
            chunks.append(chunk)
            index += 1

    return chunks
```

- [ ] **Step 5: Write failing test for GROBID reference detection**

```python
# tests/unit/test_document_chunker_paper.py

def test_grobid_references_raw_produces_reference_chunks():
    """GROBID path: references_raw in metadata must produce chunk_type='reference'."""
    from src.ingestion.chunking.document_chunker import DocumentChunker
    from src.core.types import Document

    settings = Mock()
    settings.ingestion = Mock()
    settings.ingestion.chunk_size = 500
    settings.ingestion.chunk_overlap = 50

    chunker = DocumentChunker(settings)
    ref_text = "[1] Smith, J. et al. Nature, 2020.\n[2] Doe, A. et al. Science, 2021.\n"
    doc = Document(
        id="test_doc",
        text="# Abstract\n\nSome abstract.\n\n# Introduction\n\nBody text here.",
        metadata={
            "grobid_sections": [
                {"heading": "Abstract", "paragraphs": ["Some abstract."], "level": 1},
                {"heading": "Introduction", "paragraphs": ["Body text here."], "level": 1},
            ],
            "references_raw": ref_text,
        }
    )
    chunks = chunker.split_document(doc)

    ref_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "reference"]
    assert len(ref_chunks) >= 1, "references_raw should produce reference-type chunks"
    assert any("Smith" in c.text for c in ref_chunks)
```

- [ ] **Step 6: Run test to verify it fails**

```bash
python -m pytest tests/unit/test_document_chunker_paper.py::test_grobid_references_raw_produces_reference_chunks -v
```
Expected: FAIL

- [ ] **Step 7: Modify `_split_paper_document_grobid` to add reference chunks**

Append at the end of `_split_paper_document_grobid`, before `return chunks` (after line 258):

```python
        # -- 5. References section (from loader-extracted references_raw) --
        references_raw = metadata.get("references_raw", "")
        if references_raw:
            ref_fragments = self._splitter.split_text(references_raw)
            for text in ref_fragments:
                chunk_id = self._generate_chunk_id(document.id, index, text)
                chunk_meta = self._inherit_metadata(document, index, text)
                chunk_meta["chunk_type"] = "reference"
                chunk_meta["section"] = "References"
                chunks.append(Chunk(id=chunk_id, text=text, metadata=chunk_meta))
                index += 1

        return chunks
```

- [ ] **Step 8: Run all chunker tests**

```bash
python -m pytest tests/unit/test_document_chunker_paper.py -v
```
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/ingestion/chunking/document_chunker.py tests/unit/test_document_chunker_paper.py
git commit -m "feat: mark reference/bibliography chunks with chunk_type='reference'"
```

---

### Task 3: Apply reference weight penalty in HybridSearch

**Files:**
- Modify: `src/core/query_engine/hybrid_search.py:60-79` (HybridSearchConfig)
- Modify: `src/core/query_engine/hybrid_search.py:177-201` (_extract_config)
- Modify: `src/core/query_engine/hybrid_search.py:292-297` (search method, after step 5)
- Create: `tests/unit/test_hybrid_search.py` (new file)

- [ ] **Step 1: Add `reference_weight` to HybridSearchConfig**

```python
# src/core/query_engine/hybrid_search.py, add to HybridSearchConfig dataclass (line 72)
@dataclass
class HybridSearchConfig:
    dense_top_k: int = 20
    sparse_top_k: int = 20
    fusion_top_k: int = 10
    enable_dense: bool = True
    enable_sparse: bool = True
    parallel_retrieval: bool = True
    metadata_filter_post: bool = True
    reference_weight: float = 0.3  # multiplier for reference chunks (0=exclude, 1=no demotion)
```

- [ ] **Step 2: Extract `reference_weight` from settings in `_extract_config`**

Add after the `metadata_filter_post` line (around line 200):

```python
        return HybridSearchConfig(
            dense_top_k=getattr(retrieval_config, 'dense_top_k', 20),
            sparse_top_k=getattr(retrieval_config, 'sparse_top_k', 20),
            fusion_top_k=getattr(retrieval_config, 'fusion_top_k', 10),
            enable_dense=True,
            enable_sparse=True,
            parallel_retrieval=True,
            metadata_filter_post=True,
            reference_weight=getattr(retrieval_config, 'reference_weight', 0.3),
        )
```

- [ ] **Step 3: Add `_apply_reference_demotion` method to HybridSearch**

```python
# src/core/query_engine/hybrid_search.py, add after _apply_metadata_filters method (after line 747)

def _apply_reference_demotion(
    self,
    results: List[RetrievalResult],
) -> List[RetrievalResult]:
    """Apply weight penalty to reference/bibliography chunks.

    Reference chunks marked with chunk_type='reference' get their score
    multiplied by reference_weight (default 0.3) to lower their priority
    in search results.

    Args:
        results: Fused results to process.

    Returns:
        Results with reference chunk scores penalized, re-sorted.
    """
    weight = self.config.reference_weight
    if weight >= 1.0 or not results:
        return results

    for r in results:
        if r.metadata.get("chunk_type") == "reference":
            r.score *= weight

    results.sort(key=lambda r: (-r.score, r.chunk_id))
    return results
```

- [ ] **Step 4: Call demotion in `search()` method**

In `search()` method, insert between Step 5 (metadata filter) and Step 6 (top_k):

```python
        # Step 5: Apply post-fusion metadata filters (if any)
        if merged_filters and self.config.metadata_filter_post:
            fused_results = self._apply_metadata_filters(fused_results, merged_filters)

        # Step 5.5: Apply reference chunk demotion
        fused_results = self._apply_reference_demotion(fused_results)

        # Step 6: Limit to top_k
        final_results = fused_results[:effective_top_k]
```

- [ ] **Step 5: Write unit tests for reference demotion (new file)**

```python
# tests/unit/test_hybrid_search.py (CREATE NEW FILE)
"""Unit tests for HybridSearch reference chunk demotion."""

import pytest
from src.core.query_engine.hybrid_search import HybridSearch, HybridSearchConfig
from src.core.types import RetrievalResult


def test_reference_chunks_demoted_in_results():
    """Reference chunks should have their scores multiplied by reference_weight."""
    config = HybridSearchConfig(
        enable_dense=False,
        enable_sparse=False,
        fusion_top_k=10,
        reference_weight=0.3,
    )
    hybrid = HybridSearch(config=config)

    results = [
        RetrievalResult(
            chunk_id="body_1",
            score=0.9,
            text="Important content about deep learning.",
            metadata={"chunk_type": "body"},
        ),
        RetrievalResult(
            chunk_id="ref_1",
            score=0.85,
            text="[1] Smith et al. Deep learning survey. Nature, 2023.",
            metadata={"chunk_type": "reference"},
        ),
        RetrievalResult(
            chunk_id="body_2",
            score=0.7,
            text="Another relevant paragraph.",
            metadata={},
        ),
    ]

    demoted = hybrid._apply_reference_demotion(results)

    # ref_1 score should be 0.85 * 0.3 = 0.255
    assert demoted[-1].chunk_id == "ref_1", "Reference chunk should rank last after demotion"
    assert demoted[-1].score == pytest.approx(0.255, rel=0.01)

    # body chunks should keep original scores
    body_scores = [r.score for r in demoted if r.chunk_id == "body_1"]
    assert body_scores[0] == 0.9


def test_reference_weight_1_0_no_demotion():
    """reference_weight=1.0 should not change any scores."""
    config = HybridSearchConfig(
        enable_dense=False,
        enable_sparse=False,
        reference_weight=1.0,
    )
    hybrid = HybridSearch(config=config)

    results = [
        RetrievalResult(
            chunk_id="ref_1",
            score=0.8,
            text="[1] A reference.",
            metadata={"chunk_type": "reference"},
        ),
        RetrievalResult(
            chunk_id="body_1",
            score=0.5,
            text="Body text.",
            metadata={},
        ),
    ]

    demoted = hybrid._apply_reference_demotion(results)
    assert demoted[0].score == 0.8, "Score should be unchanged when weight=1.0"
    assert demoted[0].chunk_id == "ref_1"


def test_reference_demotion_empty_results():
    """Empty results should not cause errors."""
    config = HybridSearchConfig(reference_weight=0.3)
    hybrid = HybridSearch(config=config)
    result = hybrid._apply_reference_demotion([])
    assert result == []


def test_reference_demotion_no_reference_chunks():
    """Results without reference chunks should be unchanged."""
    config = HybridSearchConfig(reference_weight=0.3)
    hybrid = HybridSearch(config=config)

    results = [
        RetrievalResult(chunk_id="a", score=0.9, text="Body A.", metadata={}),
        RetrievalResult(chunk_id="b", score=0.5, text="Body B.", metadata={"chunk_type": "body"}),
    ]

    demoted = hybrid._apply_reference_demotion(results)
    assert demoted[0].score == 0.9
    assert demoted[1].score == 0.5
```

- [ ] **Step 9: Commit**

```bash
git add src/core/query_engine/hybrid_search.py tests/unit/test_hybrid_search.py
git commit -m "feat: apply reference_weight penalty to reference chunks in hybrid search"
```

---

### Task 4: Integration verification

**Files:**
- Run: `tests/unit/test_document_chunker_paper.py`
- Run: `tests/unit/test_hybrid_search.py`
- Run: `tests/unit/test_vector_upserter_idempotency.py`
- Run: `tests/unit/test_fusion_rrf.py`

- [ ] **Step 1: Run full unit test suite for affected components**

```bash
python -m pytest tests/unit/test_document_chunker_paper.py tests/unit/test_hybrid_search.py tests/unit/test_fusion_rrf.py tests/unit/test_vector_upserter_idempotency.py tests/unit/test_document_chunker.py tests/unit/test_config_loading.py -v
```
Expected: all PASS (skipped tests are OK)

- [ ] **Step 2: Final commit**

```bash
git add -A
git commit -m "feat: complete reference chunk demotion (mark + penalty)"
```
