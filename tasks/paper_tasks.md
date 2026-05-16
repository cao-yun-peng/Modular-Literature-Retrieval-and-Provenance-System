# Paper-oriented RAG — Tasks (T1–T6)

This file lists the implementation tasks (T1–T6) requested and the test fixtures required for each.

## T1 — PaperPdfLoader
- Goal: Implement `PaperPdfLoader` extending the PDF loader to extract TOC, sections, title/authors/affiliations, abstract, keywords, figures (with `figure_id` and captions), tables (with `table_id` and CSV-ish extraction), LaTeX equations, DOI/arXiv/publishing metadata and structured references (`bib_entry`).
- Acceptance criteria:
  - Produces `Document` with `metadata` including `title, authors, doi, venue, year, references_raw, bib_entries, images`.
  - Extracted figures saved under `data/images/{collection}/{image_id}.png` and table outputs under `data/tables/{collection}/{table_id}.json` (or CSV).
  - Unit tests pass for fixtures.
- Fixtures:
  - `tests/fixtures/sample_papers/simple_paper.pdf`
  - `tests/fixtures/sample_papers/with_figures.pdf`
  - `tests/fixtures/sample_papers/with_tables.pdf`
  - `tests/fixtures/sample_papers/with_equations.pdf`
  - `tests/fixtures/sample_papers/with_references.pdf`

## T2 — Section-aware DocumentChunker
- Goal: Extend `DocumentChunker` to support section-first chunking, figure/table anchoring, equation chunks and citation-context windows.
- Acceptance criteria:
  - Chunk boundary aligned with sections; chunks containing figures/tables include `figure_refs`/`table_refs` in metadata.
  - Deterministic `chunk_id` generation for repeated runs.
- Fixtures / expectations:
  - Use same sample_papers as T1.
  - `tests/fixtures/paper_chunk_expectations.json` (specifies expected chunk counts / sample chunk_ids for each fixture).

## T3 — TableExtractor & TableContent
- Goal: Implement `TableExtractor` to parse PDF tables into structured JSON/CSV and support `TableContent` in MCP responses.
- Acceptance criteria:
  - Extracted table schema + rows stored in `data/tables/{collection}/{table_id}.json`.
  - MCP `TableContent` format documented and testable.
- Fixtures:
  - `tests/fixtures/sample_papers/with_tables.pdf`
  - `tests/fixtures/table_expected_with_tables.json`

## T4 — ResponseBuilder citations + BibTeX export tool
- Goal: Update `ResponseBuilder` to emit structured `citations` array and add `tools/export_bibtex` to return BibTeX for matched references.
- Acceptance criteria:
  - `query_knowledge_hub` responses include `citations` with `{id, doi, title, page, chunk_id, text}`.
  - `tools/export_bibtex` returns valid BibTeX entries for given `doc_id` or query hit set.
- Fixtures:
  - `tests/fixtures/bib_entries_sample.json`

## T5 — Paper golden QA set (initial 50 QA)
- Goal: Create `tests/fixtures/paper_golden_set.json` with ~50 QA items targeting evidence retrieval and multi-modal answers.
- Format (per item): `{ "id": "q1", "question": "...", "answer": "...", "supporting_chunk_ids": ["..."] }`
- Usage: for Hit@K, MRR, Evidence Precision/Recall and QA F1 evaluation in CI.

## T6 — Tests and baseline metrics
- Goal: Add unit/integration/E2E tests for T1–T5, run evaluations on sample dataset and record baseline metrics.
- Acceptance criteria:
  - Unit tests for loaders, chunker, table extractor.
  - Integration test for ingestion→retrieval→response with citations.
  - E2E run on `paper_golden_set.json` that records `logs/eval_history.jsonl` with baseline metrics (Hit@5, MRR, QA F1).
- Fixtures / helper files:
  - `tests/fixtures/paper_golden_set.json` (from T5)
  - `tests/fixtures/paper_fixture_list.md` (see below)

---

## Fixtures list (create these under `tests/fixtures`)
- `sample_papers/simple_paper.pdf` — minimal single-page paper
- `sample_papers/with_figures.pdf` — paper with figures + captions
- `sample_papers/with_tables.pdf` — paper with tables
- `sample_papers/with_equations.pdf` — paper with LaTeX equations
- `sample_papers/with_references.pdf` — paper with a reference list and DOIs
- `table_expected_with_tables.json` — expected table extraction output
- `paper_chunk_expectations.json` — expected chunk counts / example chunk IDs
- `bib_entries_sample.json` — sample structured bib entries
- `paper_golden_set.json` — golden QA set (~50 items)

---

Placeholders will be added next under `tests/fixtures` for CI to pick up.
