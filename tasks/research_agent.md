# RA-01 — bounded research Agent

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
