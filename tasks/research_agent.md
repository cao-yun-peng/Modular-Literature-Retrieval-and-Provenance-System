# RA-01 — bounded research Agent

## RA-WEB-01 — web research integration (2026-09-17)

- Revision: 2; status: reviewed / delivered; executor: Codex. User approved the web integration plan and explicitly authorized the real DeepSeek/Embedding validation calls.
- Discovery: project-to-act --check remains unconfigured with no external ledger. Continue this feature record without project-wide initialization.
- Scope: local-only research page, structured live events and per-round snapshots, durable runs/SSE/replay, evidence navigation, report export and full-run retry. Research concurrency: one.
- Allowed paths: src/agents/, src/web_api/, web/, focused tests, docs/RESEARCH_AGENT.md, tasks/research_agent.md and validation artifacts under output/.
- Invariants: preserve frozen baseline, existing CLI/MCP behavior and existing run types; never enable web acquisition or Zotero from this API; distinguish execution state from research quality; browser disconnect does not cancel work; restart requires explicit retry.
- Contract: POST /api/v1/research-runs; GET /api/v1/research-runs/{id}/result; shared runs/events/history/retry/artifacts. Research events carry stage, phase, round, query and bounded structured data; stage failure is distinct from task failure.
- Acceptance: offline multi-round/event/failure/API/reconnect/restart/citation tests, frontend build/tests, browser verification and bounded real-model review/timeline checks when provider is available. Quality remains subject to human source review.
- Gate: web implementation / offline contracts / browser integration passed. Live review and timeline smoke tests passed; academic completeness and semantic claim support still require human review. No production-readiness claim.
- Rollback: remove research navigation/routes/dispatch registration; preserve user run records and report artifacts.

### Web verification and handoff — 2026-09-17

- RA-WEB-E01: `.venv/Scripts/python.exe -m pytest tests/unit/test_web_research.py tests/unit/test_research_agent.py tests/unit/test_research_interfaces.py tests/unit/test_research_acquisition.py tests/unit/test_web_api.py -q -o cache_dir=output/pytest-cache`; exit 0; **72 passed**. Covers live snapshots, multi-round queries, result quality, errors, input permissions, idempotency, SSE resume, restart, full retry, frozen-corpus reads, one-at-a-time dispatch and existing CLI/MCP/web regressions.
- RA-WEB-E02: `pnpm --dir web build`; exit 0. `pnpm --dir web test`; exit 0; **6 passed**. OpenAPI regenerated and TypeScript synchronized. Scoped Ruff checks and `git diff --check` passed.
- RA-WEB-E03: `node node_modules/@playwright/test/cli.js test e2e/research.spec.ts` from web/; exit 0; **3 passed**. Covers live-to-completed presentation, citation/source navigation, partial-result history/reload, narrow dark layout, and full retry. Uses controlled API responses, no model calls. Screenshots: web/test-results/research-desktop.png and research-mobile-dark.png.
- RA-WEB-E04: isolated real-model validation against the existing frozen corpus. Review `run_8f685a554d8d446db805e2baa258a9f5`: 2 rounds, 3 research-model calls, 14 evidence excerpts / 8 sources, all 14 linked, draft, no warnings. Timeline `run_16a59997d4ea4ef88cf8fa3e11380835`: 2 rounds, 3 research-model calls, 12 excerpts / 7 sources, all 12 linked, draft, no warnings. Both reports contain only known evidence IDs; timeline retains the existing source-year check. Calls for embeddings and reranking are separate from the research model-call counter.
- Initial sandbox trials could not connect externally. After explicit user approval, live review passed; the first live timeline (`run_c26e9ad37cf24eb0913f118ad17fb43a`) encountered transport/TLS interruptions. Its 11 evidence excerpts and failure events were retained, and one explicit full retry passed. This is a smoke test, not a reliability percentage or a comprehensive domain evaluation.
- RA-WEB-E05: `node output/research-web-validation/browser-check.cjs`; exit 0. Both actual report pages survived reload; their citations opened the registered source chunks and rendered original PDFs without new model calls.
- RA-WEB-E06: `.venv/Scripts/python.exe -X utf8 scripts/import_paper_baseline.py verify`; exit 0; **548 files checked, 48 documents, 993 chunks**. No re-ingestion or baseline changes.
- Evidence locations: output/research-web-validation/results.json, timeline-first-attempt.json, blocked-network-results.json, evidence.json, registry/runs/<run_id>/report.md and research.json; actual-page screenshots review-live.png / timeline-live.png. File hashes and Git base in evidence.json scope the evidence to this implementation; revalidate after related changes.
- Handoff: existing idle workbench was restarted on 127.0.0.1:8765; its OpenAPI now exposes /api/v1/research-runs. Start from /research. Validation runs were isolated from the normal workbench registry. First version remains local-corpus-only, with full restart retry and no hard cancellation/checkpoint resume. Transient provider failures remain visible and require retry.

## RA-01 — original prototype record

- Revision: 2; status: reviewed / prototype delivered; executor: Codex; started: 2026-09-06.
- Scope: a small internal research prototype (L1), on existing retrieval/ingestion contracts.
- Governance discovery: project-to-act --check returned unconfigured, no detected external ledger. This bounded feature uses this task record; no project-wide governance adoption.
- Current stage: 6 (real-model quality evaluation remains pending); stage 5 implementation gate passed against offline contracts. Earlier architecture evidence is the inspected code and linked primary sources in docs/RESEARCH_AGENT.md, not historical production gates.
- Intent/allowed paths: src/agents/, src/integrations/arxiv.py, src/integrations/zotero/connector.py, src/mcp_server/tools/research_topic.py, protocol registration, scripts/research.py, focused tests and documentation.
- Context: QueryKnowledgeHubTool.execute returns MCPToolResponse.evidence_bundle; IngestionPipeline.run accepts source_metadata; LLMFactory.create provides synchronous chat; ZoteroLocalClient remains read-only.
- Contract: bounded query/rewrite/retrieve/assess loop; deduplicated source evidence; structured cited review/timeline; explicit arXiv acquisition; optional separately authorized Zotero Connector import. No arbitrary URL download or generated bibliographic records.
- Invariants: preserve existing user changes; original question remains in each model request; model never grants tool permissions; zero evidence produces no substantive review; reference IDs and timeline dates are checked; failures are visible.
- Limits: 1–5 rounds, 1–3 queries/round, <=30 excerpts, <=5 downloaded papers/run, bounded model output and network requests.
- Acceptance: deterministic offline tests for loop decisions, no-progress termination, empty/error retrieval, citation/date rejection, arXiv parsing/download validation, acquisition re-retrieval, Zotero write gating, CLI/MCP contracts; existing affected regression tests pass.
- Real model quality, live downloads, live Zotero writes and production readiness require separate evidence; offline tests do not establish scholarly completeness.
- Next gate: domain-specific real-model quality evaluation; no production release claim.

## Verification evidence / handoff

- RA-E01, 2026-09-06: `.venv/Scripts/python.exe -m pytest -p no:cacheprovider tests/unit/test_research_agent.py tests/unit/test_research_acquisition.py tests/unit/test_research_interfaces.py tests/unit/test_response_builder.py tests/unit/test_protocol_handler.py tests/unit/test_hierarchical_evidence.py tests/unit/test_zotero_integration.py -q`; exit 0; **96 passed** in 69.33 seconds. One non-failing warning: disabling pytest cache plugin leaves the existing `cache_dir` setting unknown. The first focused run also passed 34 tests, with a cache-directory permissions warning.
- RA-E02, 2026-09-06: Ruff check over all new Python modules and three new test files; exit 0, all checks passed. CLI `scripts/research.py --help` exit 0. File hashes and Git base are in `tasks/research_agent_evidence.json`.
- RA-E03, 2026-09-06: live, read-only `ArxivClient(timeout=20).search('retrieval augmented generation', 1)`; exit 0; parsed `2506.06962v3`, published `2025-06-08T01:33:05Z`. This verifies API transport/response parsing, not relevance or review quality. No PDFs downloaded and no Zotero items created in this check.
- Evidence validity: applies to the recorded file hashes and current dependency environment; rerun affected checks when these change. API evidence is a point-in-time observation.
- Gate decision: implementation/offline contracts passed; scholarly coverage, claim entailment, PDF ingestion end-to-end, Zotero attachment persistence, cost/latency and production controls remain unverified.
- Manual review: confirmed model permissions cannot enable web/Zotero, per-call retrieval instances, no cross-collection skip via global ingestion hash cache, separate RAG/Zotero failure states, pending import journal instead of blind retries, no-content response on missing relevant evidence.
- Preserved existing user modifications: README edits, the existing Zotero walkthrough, and deleted tracked test temporary files were not altered or restored.
- Handoff: use `docs/RESEARCH_AGENT.md` and `scripts/research.py --help`. Owner for the next domain-quality acceptance is the project maintainer. Build a small representative corpus and manually inspect claim support, missing seminal work, publication dates and Zotero attachments before any production decision.
- Rollback: unregister `research_topic` and remove the new modules/scripts; existing query and Zotero read-only flows stay available. Generated run artifacts and imported library items are user data and must not be deleted as an automatic code rollback.
