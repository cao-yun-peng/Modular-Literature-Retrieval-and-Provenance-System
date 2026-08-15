"""Citation Generator for generating structured reference information.

This module generates citation information from retrieval results,
enabling MCP tools to return properly formatted references that
can be used by AI assistants for source attribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from src.core.types import RetrievalResult


@dataclass
class Citation:
    """Represents a single citation/reference.
    
    Attributes:
        index: Citation index number (1-based, for display as [1], [2], etc.)
        chunk_id: Unique identifier for the source chunk
        source: Source file path or document name
        page: Page number in source document (if applicable)
        score: Relevance score from retrieval
        text_snippet: Short excerpt from the referenced content
        metadata: Additional metadata (title, section, etc.)
    """
    index: int
    chunk_id: str
    source: str
    score: float
    text_snippet: str
    page: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "index": self.index,
            "chunk_id": self.chunk_id,
            "source": self.source,
            "score": round(self.score, 4),
            "text_snippet": self.text_snippet,
            "id": self.metadata.get("id") if self.metadata else self.chunk_id,
            "text": self.text_snippet,
        }
        if self.metadata:
            if "doi" in self.metadata:
                result["doi"] = self.metadata.get("doi")
            if "title" in self.metadata:
                result["title"] = self.metadata.get("title")
            for field_name in (
                "citation_key",
                "zotero_item_key",
                "zotero_attachment_key",
                "parent_id",
                "section_path",
                "page_start",
                "page_end",
            ):
                if field_name in self.metadata:
                    result[field_name] = self.metadata.get(field_name)
        if self.page is not None:
            result["page"] = self.page
        citation_key = result.get("citation_key")
        page_start = result.get("page_start", self.page)
        page_end = result.get("page_end", page_start)
        locator = None
        if page_start is not None:
            locator = (
                f"p. {page_start}"
                if page_end in (None, page_start)
                else f"pp. {page_start}–{page_end}"
            )
            result["locator"] = locator
        if citation_key:
            suffix = f", {locator}" if locator else ""
            result["markdown"] = f"[@{citation_key}{suffix}]"
        if self.metadata:
            result["metadata"] = self.metadata
        return result


class CitationGenerator:
    """Generates citation information from retrieval results.
    
    This class transforms RetrievalResult objects into Citation objects
    with proper indexing and metadata extraction.
    
    Example:
        >>> generator = CitationGenerator()
        >>> results = [RetrievalResult(chunk_id="doc1_001", score=0.95, ...)]
        >>> citations = generator.generate(results)
        >>> print(citations[0].index)  # 1
        >>> print(citations[0].source)  # "docs/guide.pdf"
    """
    
    def __init__(
        self,
        snippet_max_length: int = 200,
        include_metadata_fields: Optional[List[str]] = None,
    ) -> None:
        """Initialize CitationGenerator.
        
        Args:
            snippet_max_length: Maximum characters for text_snippet (default: 200)
            include_metadata_fields: Optional list of metadata fields to include.
                If None, includes 'title', 'section', 'chunk_index'.
        """
        self.snippet_max_length = snippet_max_length
        self.include_metadata_fields = include_metadata_fields or [
            "title",
            "section",
            "section_path",
            "chunk_index",
            "doc_type",
            "doi",
            "citation_key",
            "zotero_item_key",
            "zotero_attachment_key",
            "parent_id",
            "page_start",
            "page_end",
        ]
    
    def generate(self, results: List[RetrievalResult]) -> List[Citation]:
        """Generate citations from retrieval results.
        
        Args:
            results: List of RetrievalResult objects from search.
            
        Returns:
            List of Citation objects with 1-based indexing.
        """
        citations = []
        
        for idx, result in enumerate(results, start=1):
            citation = self._create_citation(idx, result)
            citations.append(citation)
        
        return citations
    
    def _create_citation(self, index: int, result: RetrievalResult) -> Citation:
        """Create a Citation from a single RetrievalResult.
        
        Args:
            index: 1-based citation index.
            result: RetrievalResult to convert.
            
        Returns:
            Citation object with extracted information.
        """
        metadata = result.metadata or {}
        
        # Extract source path
        source = metadata.get("source_path", "unknown")
        
        # Extract page number (may be int or string)
        page = (
            metadata.get("page")
            or metadata.get("page_num")
            or metadata.get("page_start")
        )
        if page is not None:
            try:
                page = int(page)
            except (ValueError, TypeError):
                page = None
        
        # Generate text snippet
        text_snippet = self._generate_snippet(result.text)
        
        # Extract selected metadata fields
        extra_metadata = {}
        for field_name in self.include_metadata_fields:
            if field_name in metadata and field_name not in ("source_path", "page", "page_num"):
                extra_metadata[field_name] = metadata[field_name]
        
        return Citation(
            index=index,
            chunk_id=result.chunk_id,
            source=source,
            score=result.score,
            text_snippet=text_snippet,
            page=page,
            metadata=extra_metadata,
        )
    
    def _generate_snippet(self, text: str) -> str:
        """Generate a truncated snippet from text.
        
        Args:
            text: Full text content.
            
        Returns:
            Truncated text with ellipsis if needed.
        """
        if not text:
            return ""
        
        # Clean up whitespace
        cleaned = " ".join(text.split())
        
        if len(cleaned) <= self.snippet_max_length:
            return cleaned
        
        # Truncate and add ellipsis
        truncated = cleaned[:self.snippet_max_length].rsplit(" ", 1)[0]
        return truncated + "..."
    
    def format_citation_marker(self, index: int) -> str:
        """Format a citation marker for inline use.
        
        Args:
            index: 1-based citation index.
            
        Returns:
            Formatted marker like "[1]".
        """
        return f"[{index}]"
