# Evidence benchmark implementation — 2026-09-09

Canonical task record for the paper corpus and evidence benchmark; current direction precedes historical plans.

## Current cleanup direction — 2026-09-16

- User explicitly requested deleting previous/test stored corpora and keeping the current 48-paper baseline, then extended the request to unused code outside the current workflow. This supersedes historical instructions below to preserve the old runtime collections and draft v1/v2 data.
- Preserve `papers48_baseline`, its live Chroma/BM25/Workbench stores, all 48 originals, frozen snapshot, MinerU caches for both originals and page segments, and the new 20-question dataset/results. Source PDFs outside runtime storage, credentials, dependencies, functional modules and regression fixtures are outside the deletion scope.
- Remove obsolete runtime indices, old evaluation artifacts and single-paper validation outputs; filter shared history/log records by verified collection/input identity. Remove independently obsolete debug/translation/validation scripts and the Dashboard page that only replayed retired validation results; update current entrypoint documentation.
- Inventory completed: 50 targets / 7,093 files / 1,761.07 MiB, including 8 obsolete code/config files and 19 old collection instances. Original-page-segment cache hashes were checked against the baseline plan; 21 extra cache directories belong to current page segments and are protected.
- Automatic approval review rejected the irreversible batch deletion because it required explicit confirmation of this concrete list. The deletion command did not execute. A user confirmation question is pending; do not execute any dependent deletion or shared-record pruning until it is answered.
- Reversible work completed: removed the retired Dashboard navigation entry and updated current startup/verification instructions; historical reports are marked accordingly. Offline verification: 103 targeted tests passed (exit 0), Dashboard lint passed, baseline verification passed all 548 hashes, pilot validation passed 20 cases/26 spans with 0 human approvals. Reviewable inventory: `output/papers48-cleanup-review.md`; full paths/protection hashes: `output/papers48-cleanup-plan.json`; verification: `output/papers48-cleanup-verification.json`. Status remains pending deletion approval, not complete.

## Current evaluation direction — 2026-09-16, pilot20

- User requested rebuilding the evaluation dataset and evaluator from the newly frozen baseline, starting with 20 questions. This follows corpus freezing; the earlier corpus-only scope is now completed.
- Created `data/paper_benchmark/papers48-pilot20-v1/dataset.json`: 20 newly source-authored development questions (14 Chinese, 6 English), 26 required evidence groups, 19 supporting papers within the full 48-paper retrieval corpus. Types: mechanism 5, exact entity 3, method/conditions 5, findings/limitations 4, cross-paper 3.
- All 26 frozen chunk IDs, document IDs, exact character offsets and quotes validated. All 26 have original-PDF page excerpts; 20 are exact after whitespace/dehyphen normalization, 6 are partial alignment candidates requiring manual checking. All 20 human review states remain pending.
- Added `scripts/pilot_benchmark.py` and separate frozen-pilot modules: validation, review HTML/export/import, read-only BM25/Dense/RRF retrieval, fingerprint-bound saved runs and offline scoring replay. No old questions or old scores reused. Original 120-question formal validation gates were not weakened.
- Scoring reports known-target document hit/recall, required-evidence-group recall, all-evidence success and TargetRR. Ordinary MRR/nDCG remain unavailable without reviewed relevance judgments. No automatic answer judge, no unanswerable/handoff/service/MCP quality claims.
- Initial query embedding failed due to network transport restrictions. Automatic approval then timed out once and rejected once because historical paper-embedding consent did not explicitly cover the 20 new queries. User subsequently explicitly authorized sending these 20 queries to DashScope and completing Dense/RRF runs; the authorized call succeeded (2 requests, 730 reported tokens). Authorization and usage scope: queries only, no answers, evidence or PDFs. Cached query vectors support subsequent local runs.
- Executed 60/60 queries across BM25, exact-cosine Dense over frozen exports, and RRF; per-case and aggregate scores replay exactly. Provisional EvidenceRecall@10: .300 / .825 / .875; AllEvidence@10: .300 / .800 / .850. These are unreviewed development diagnostics, not resume-ready results or Chroma ANN/service benchmarks.
- Verification: 15 targeted tests passed; new-file lint passed; review JavaScript syntax passed. Frozen baseline verification again passed all 548 artifact hashes. Browser automation refused the local-file review URL; HTML delivered with this preview limitation and a Markdown question sheet.
- Deliverables and instructions: `docs/PAPERS48_PILOT20.md`; dataset, `QUESTIONS.md`, `review.html`, usage, saved runs and scores under the new pilot directory.
- Next gate: human review of the 20 questions/answers/evidence, then candidate relevance pooling and independently held-out evaluation. Preserve this development run and do not change labels merely to improve scores.

## Completed corpus freezing — 2026-09-16

- User explicitly requested re-importing all 48 original papers through the current project pipeline and fixing that corpus/index as the baseline before proceeding with evaluation.
- Current pipeline: MinerU Agent, structured 2,500-token chunks with 200-token target overlap, DashScope text-embedding-v3 (1,024 dimensions), Chroma and BM25. Previous v1 chunks and scores are historical and do not describe this baseline.
- Scope is corpus/index freezing only; question review and retrieval-quality evaluation remain incomplete.
- Preflight verified all 48 original SHA256 values and all 580 physical pages (163,527,855 bytes). Nine documents require lossless page segments; 60 parser inputs total. Text and rendered pixels matched the originals on all 238 segmented pages.
- Prepared manifest: `data/baselines/papers48-20260916/plan.json`. Remote destinations are MinerU for PDFs and configured DashScope for parsed text embeddings. Successful existing parser/embedding caches may be reused.
- The initial bulk-upload command was rejected before execution. The user subsequently explicitly authorized sending all 48 complete PDFs to MinerU and all parsed text to DashScope, including use of the configured API quota, in this task on 2026-09-16. This authorization applies to the above manifest and supersedes the historical pending-upload restriction for this current ingestion workflow.
- Added oversized-document handling (50 MB/200-page application cap; each MinerU request retains its 10 MB/20-page cap), resumable import, and ingestion guards for frozen baselines.
- Completed: 48/48 papers imported into `papers48_baseline`; 580 original pages; 993 chunks and 993 finite 1,024-dimensional vectors; maximum chunk length 2,500 tokens. All source hashes match, and the registry/Chroma/BM25 chunk-ID sets agree.
- Validation: 68 targeted tests passed; 21 workbench tests passed again after collection isolation; React production build passed. Freeze succeeded at `2026-09-16T04:58:13.893761+00:00`; verification checked 548 artifact hashes. Live checks confirmed 48 successful documents, readable PDFs/chunks, and HTTP 409 for both ingestion and uploads after freeze. Evidence: `output/baseline-live-verification.json`, `data/baselines/papers48-20260916/manifest.json`.
- One PDF upload suffered a connection timeout. The existing MinerU task was checked and remained `waiting-file`; its record was archived before resubmission. The retry succeeded; the original failed run and recovery evidence remain in the snapshot. `scripts/resume_paper_baseline.py` resumed through the existing retry API and skipped successful documents; parsing/chunking/embedding settings and implementation stayed unchanged.
- Acceptance: exactly 48 registered originals, all ingestion runs successful, matching chunk IDs in Chroma/BM25, 1,024-dimensional finite vectors, chunks at most 2,500 tokens, complete immutable artifact hashes, and blocked subsequent ingestion into the baseline collection. A frozen index is not a retrieval-quality result.
- Collection preflight found one unrelated paper in the previous workbench. The requested corpus therefore uses a new `papers48_baseline` collection and an isolated registry selected by collection name. The original two-paper registry and indexes remain intact. Default configuration now selects the 48-paper baseline.
- The frozen snapshot includes the exact ingestion source/configuration, originals, raw and normalized Markdown, chunks, vectors, BM25 data, traces, and registry backup. Retrieval-quality metrics and human labels are still not evaluated for this new baseline.
- Final protection: all 550 snapshot files marked read-only, followed by a second successful 548-file hash verification. Stored-vector readback passed for one representative chunk from each of the 48 papers without external model calls (`output/baseline-vector-readback.json`); this is an index-readability check, not retrieval quality evaluation.

## Historical benchmark plan — 2026-09-09

- Scope: corpus inventory; source-anchored labels; retrieval/service evaluation;
  authoring/review/freeze; controlled comparisons and replayable reports.
- Corpus: 49 files / 48 unique PDFs / 580 physical pages (initial read-only audit).
- Human gate: never mark model-generated questions or judgments as human reviewed.
- External dependency: user configured DeepSeek; authoring calls completed with recorded usage.
- Preserve existing user edits to citation_generator.py and protocol_handler.py.
- Status: software implemented and validated; source indexing and draft development runs complete. Human labels and formal quality baseline remain unverified.

Acceptance: hand-calculated scorer tests, legacy CLI tests, service adapter and
protocol tests; actual corpus preparation and indexing; export reviewable pilot;
formal freeze requires 120 human-approved questions and 24 second reviews.

## Verified implementation evidence

- 49 source files, 48 content-deduplicated PDFs, 580 physical pages; all 48 ingested successfully into an isolated index of 11,124 chunks.
- 179 valid candidates from 180 requests; one failed candidate replaced by its same-slot alternative. 120 draft questions meet all language/type/split quotas. No human approvals recorded.
- 139 relevant unit/integration tests passed (`data/paper_benchmark/verification_final.log`); lint and diff whitespace checks passed.
- Corpus, normalized source text, label, vector, BM25 and hierarchy hashes bind artifacts. Real service adapter and MCP stdio fixture cover serialization and absent attachment identity.
- BM25 inversion changed from an exhaustive term/document scan to a single pass, with exact postings and ranking equivalence tests. This resolved an observed ingestion bottleneck.
- Data root: `data/paper_benchmark/v1` (ignored by Git, preserve separately). Manual: `docs/PAPER_BENCHMARK_MANUAL.md`.
- Actual runs: 360 development retrieval requests, 40 pilot service requests and 720 pilot ablation score rows (3 repeats); all operationally successful, all provisional.
- Replayed 360 saved responses without new model calls; all common metrics identical. Every pilot ablation used a consistent per-case candidate-pool ID.
- Review artifacts: 120 pending questions, 1,726 pending development relevance judgments, and 262 source-span mapping proposals (40 exact, 168 partial, 54 missing).
- Reports: `data/paper_benchmark/v1/BASELINE_DIAGNOSTIC_REPORT.md`, six comparisons, cost accounting and delivery checks. No test-split queries executed and no final strategy selected.

## Remaining acceptance work

- Human calibration of 20 pilot questions; review all 120 questions and paper families; 24 documented blind second reviews.
- Review pooled relevance, search for omitted source evidence, and version judgments.
- Freeze labels, choose final configurations on development data, then run the held-out test set. Draft scores must not be presented as formal project quality.
- Controlled host/model cold-start performance experiment remains distinct from first-process/warm request measurements.

## Follow-up: token chunks and DashScope — 2026-09-09

- User clarified a fixed token window, not a fixed number of chunks per paper; configured `text-embedding-v3` and DashScope key, and authorized small model probes and image-description integration.
- Implemented isolated `config/settings.dashscope.yaml`: 2,500 local `cl100k_base` tokens / 200 overlap, 1,024-dimensional DashScope embedding, Qwen-VL image notes, preserved PDF physical pages and original text. Main/default profile and v1 index retained.
- Local preview: 48 papers / 372 chunks; median 6, range 3–37, 13 papers with 5–6 chunks, 33 with 4–8. Counts are preview results, not a built index. `cl100k_base` is a declared proxy; actual provider usage is recorded separately.
- Two real paper text snippets and two images passed API probes. Vision outputs remain unreviewed model annotations, separate from source evidence and excluded from gold coverage credit.
- Images: 2,243 extracted occurrences, 804 eligible by size, 762 unique eligible images. Small fragments have explicit skip status. Vector-only figures and exact figure/paragraph alignment are not covered.
- Validation: 233 + 107 = 340 relevant tests passed, including actual Chroma storage with mocked providers, visual metadata roundtrip, original-text preservation and MCP serialization. Logs: `dashscope_final_tests.log`, `dashscope_store_rerank_tests.log` under `data/paper_benchmark`.
- FULL UPLOAD PENDING: automatic approval review rejected sending all 48 papers and hundreds of images to DashScope without explicit authorization at that payload scope and destination. Rejected command did not run. Complete upload manifest: `data/paper_benchmark/v2-qwen2500/upload_plan.json`. Do not bypass or retry bulk external calls before the user explicitly approves this scope.
- Report: `docs/DASHSCOPE_PAPER_INGESTION.md`. New index mapping, development comparisons and all human/freeze acceptance work remain outstanding. No new formal benchmark result is claimed.
