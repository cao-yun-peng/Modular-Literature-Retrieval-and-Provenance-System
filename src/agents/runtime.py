"""Lazy production adapters; importing this module does not create clients."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.agents.research import ResearchAgent, ResearchOptions


def build_agent(settings, *, options=None, download_dir=None, zotero=None):
    from src.libs.llm.llm_factory import LLMFactory
    from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool

    options = options or ResearchOptions()
    query_tool = QueryKnowledgeHubTool(settings=settings)
    overrides = {}
    for name in ("api_key", "base_url"):
        value = getattr(settings.llm, name, None)
        if value:
            overrides[name] = value
    llm = LLMFactory.create(settings, **overrides)
    acquire = None
    if options.allow_web:
        if download_dir is None:
            raise ValueError("Web research requires a download directory")
        from src.integrations.arxiv import ArxivClient, LiteratureAcquirer

        def ingest(path, metadata, collection):
            from src.ingestion.pipeline import IngestionPipeline

            # Existing file-integrity cache is global across collections. Force
            # this explicit acquisition so a PDF indexed elsewhere is not skipped.
            pipeline = IngestionPipeline(
                settings, collection=collection, force=True, use_paper_loader=True
            )
            try:
                return pipeline.run(str(path), source_metadata=metadata)
            finally:
                pipeline.close()

        service = LiteratureAcquirer(ArxivClient(), Path(download_dir), ingest, zotero=zotero)

        async def acquire(query, collection, limit):
            # Do not abandon a writing worker via wait_for: return its final status.
            # Requests have their own transport timeouts; parser/ingestion runtime
            # is not a hard wall-clock bound in this prototype.
            return await asyncio.to_thread(service.acquire, query, collection, limit)

    return ResearchAgent(llm, query_tool.execute, acquire=acquire, options=options)
