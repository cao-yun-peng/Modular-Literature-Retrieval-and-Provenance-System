"""Document chunking module - adapts libs.splitter for business layer.

This module serves as the adapter layer between libs.splitter (pure text splitting)
and Ingestion Pipeline (business object transformation). It transforms Document
objects into Chunk objects with proper ID generation, metadata inheritance, and
traceability.

Core Value-Add (vs libs.splitter):
1. Chunk ID Generation: Deterministic and unique IDs for each chunk
2. Metadata Inheritance: Propagates Document metadata to all chunks
3. chunk_index: Records sequential position within document
4. source_ref: Establishes parent-child traceability
5. Type Conversion: str → Chunk object (core.types contract)

Design Principles:
- Adapter Pattern: Bridges text splitter tool with business objects
- Config-Driven: Uses SplitterFactory for configuration-based strategy selection
- Deterministic: Same Document produces same Chunk IDs on repeat splits
- Type-Safe: Enforces core.types.Chunk contract
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, List

from src.core.types import Chunk, Document
from src.libs.splitter.splitter_factory import SplitterFactory

if TYPE_CHECKING:
    from src.core.settings import Settings

_REFERENCE_SECTION_PATTERN = re.compile(
    r"^(references?|bibliography|参考文献|引用文献|参考)\s*$", re.IGNORECASE
)


class DocumentChunker:
    """Converts Documents into Chunks with business-level enrichment.
    
    This class wraps a text splitter (from libs) and adds business logic:
    - Generates stable chunk IDs
    - Inherits and extends metadata
    - Maintains document traceability
    
    Attributes:
        _splitter: The underlying text splitter from libs layer
        _settings: Configuration settings for chunking behavior
    
    Example:
        >>> from src.core.settings import load_settings
        >>> from src.core.types import Document
        >>> settings = load_settings("config/settings.yaml")
        >>> chunker = DocumentChunker(settings)
        >>> document = Document(
        ...     id="doc_123",
        ...     text="Long document content...",
        ...     metadata={"source_path": "data/report.pdf"}
        ... )
        >>> chunks = chunker.split_document(document)
        >>> print(f"Generated {len(chunks)} chunks")
        >>> print(f"First chunk ID: {chunks[0].id}")
        >>> print(f"First chunk index: {chunks[0].metadata['chunk_index']}")
    """
    
    def __init__(self, settings: Settings):
        """Initialize DocumentChunker with configuration.
        
        Args:
            settings: Configuration settings containing splitter configuration.
                     The splitter config is expected at settings.splitter.*
        
        Raises:
            ValueError: If splitter configuration is invalid or provider unknown
        """
        self._settings = settings
        self._splitter = SplitterFactory.create(settings)
    
    def split_document(self, document: Document) -> List[Chunk]:
        """Split a Document into Chunks with full business enrichment.
        
        This is the main entry point that orchestrates the transformation:
        1. Uses underlying splitter to get text fragments
        2. Generates deterministic IDs for each chunk
        3. Inherits and extends metadata from document
        4. Creates Chunk objects conforming to core.types contract
        
        Args:
            document: Source document to split into chunks
        
        Returns:
            List of Chunk objects with:
            - Unique, deterministic IDs
            - Inherited metadata + chunk_index + source_ref
            - Proper type contract (core.types.Chunk)
        
        Raises:
            ValueError: If document has no text or invalid structure
        
        Example:
            >>> doc = Document(
            ...     id="doc_abc",
            ...     text="Section 1 content.\\n\\nSection 2 content.",
            ...     metadata={"source_path": "file.pdf", "title": "Report"}
            ... )
            >>> chunker = DocumentChunker(settings)
            >>> chunks = chunker.split_document(doc)
            >>> len(chunks) >= 1
            True
            >>> chunks[0].metadata["source_path"]
            'file.pdf'
            >>> chunks[0].metadata["chunk_index"]
            0
            >>> chunks[0].metadata["source_ref"]
            'doc_abc'
        """
        if not document.text or not document.text.strip():
            raise ValueError(f"Document {document.id} has no text content to split")

        if self._settings.ingestion.splitter == "token":
            if document.metadata.get("paper_sections") is not None:
                return self._split_structured_tokens(document)
            return self._split_token_document(document)
        
        # Paper-aware mode: section-first chunking
        if self._is_paper_document(document):
            return self._split_paper_document(document)

        # Step 1: Use underlying splitter to get text fragments
        text_fragments = self._splitter.split_text(document.text)
        
        if not text_fragments:
            raise ValueError(
                f"Splitter returned no chunks for document {document.id}. "
                f"Text length: {len(document.text)}"
            )
        
        # Step 2: Transform text fragments into Chunk objects with enrichment
        chunks: List[Chunk] = []
        for index, text in enumerate(text_fragments):
            chunk_id = self._generate_chunk_id(document.id, index, text)
            chunk_metadata = self._inherit_metadata(document, index, text)
            
            chunk = Chunk(
                id=chunk_id,
                text=text,
                metadata=chunk_metadata
            )
            chunks.append(chunk)
        
        return chunks

    def _split_token_document(self, document: Document) -> List[Chunk]:
        chunks = []
        for index, (start, end) in enumerate(self._splitter.split_spans(document.text)):
            text = document.text[start:end]
            metadata = self._inherit_metadata(document, index, text)
            for key in ("page", "page_num", "page_start", "page_end", "page_ranges"):
                metadata.pop(key, None)
            pages = [p for p in document.metadata.get("page_ranges", [])
                     if start < p["end"] and end > p["start"]]
            page_numbers = {p["page"] for p in pages}
            images = [img for img in document.metadata.get("images", [])
                      if img.get("page") in page_numbers]
            metadata.update(
                chunk_type="paper_text", chunking_strategy="token-document-v1",
                tokenizer=self._splitter.tokenizer_name, token_count=self._splitter.count(text),
                chunk_size_tokens=self._splitter.chunk_size,
                overlap_tokens=self._splitter.chunk_overlap,
                start_offset=start, end_offset=end, preserve_source_text=True,
                image_refs=[img["id"] for img in images], images=images,
                retrieval_text_version="token-document-v1", corpus_schema_version="3.0",
            )
            if pages:
                metadata.update(page_start=min(page_numbers), page_end=max(page_numbers))
            chunks.append(Chunk(id=self._generate_chunk_id(document.id, index, text),
                text=text, metadata=metadata, start_offset=start, end_offset=end, source_ref=document.id))
        return chunks

    def _is_paper_document(self, document: Document) -> bool:
        metadata = document.metadata or {}
        return bool(metadata.get("paper_mode") or metadata.get("doc_type") == "paper")

    def _split_paper_document(self, document: Document) -> List[Chunk]:
        """Dispatch to structured or legacy paper chunking.

        When provider-neutral or legacy GROBID sections are available we create:
        1. Title + Abstract chunk(s)
        2. Per-figure chunks (for linked retrieval)
        3. Per-table chunks (for linked retrieval)
        4. Body section chunks with figure/table reference placeholders

        Otherwise falls back to heading-based section splitting.
        """
        metadata = document.metadata or {}
        if metadata.get("paper_sections") or metadata.get("grobid_sections"):
            return self._split_structured_tokens(document)
        return self._split_paper_document_legacy(document)

    def _split_structured_tokens(self, document: Document) -> List[Chunk]:
        from src.libs.splitter.token_splitter import TokenSplitter
        from src.libs.splitter.structured_token import structured_spans, pack_references
        splitter = TokenSplitter(self._settings)
        meta = document.metadata
        figures = meta.get("paper_figures", meta.get("grobid_figures", []))
        tables = meta.get("paper_tables", meta.get("grobid_tables", []))
        sections = meta.get("paper_sections", meta.get("grobid_sections", []))
        chunks = []

        def emit(text, kind, section="", **extra):
            pieces = ((t, 0) for t in pack_references(splitter, text)) if kind == "reference" else (
                (text[a:b], overlap) for a, b, overlap in structured_spans(splitter, text))
            for part, (piece, overlap) in enumerate(pieces):
                index = len(chunks)
                metadata = self._inherit_metadata(document, index, piece)
                for key in ("paper_sections", "grobid_sections", "references_raw", "abstract",
                            "page", "page_num", "page_start", "page_end", "page_ranges"):
                    metadata.pop(key, None)
                metadata.update(chunk_type=kind, section=section, part_index=part,
                    tokenizer=splitter.tokenizer_name, token_count=splitter.count(piece),
                    chunk_size_tokens=splitter.chunk_size, overlap_tokens=splitter.chunk_overlap,
                    actual_overlap_tokens=overlap, preserve_source_text=True,
                    chunking_strategy="structured-token-v1", retrieval_text_version="structured-token-v1",
                    corpus_schema_version="4.0", **extra)
                metadata["linked_figures"] = sorted(set(re.findall(r"\[FIG_REF:\s*([^\]]+)\]", piece)))
                metadata["linked_tables"] = sorted(set(re.findall(r"\[TABLE_REF:\s*([^\]]+)\]", piece)))
                for key in ("linked_figures", "linked_tables"):
                    if not metadata[key]:
                        metadata.pop(key)
                self._enrich_paper_chunk_metadata(metadata, piece)
                chunks.append(Chunk(id=self._generate_chunk_id(document.id, index, piece),
                    text=piece, metadata=metadata, source_ref=document.id))

        title, abstract = meta.get("title", ""), meta.get("abstract", "")
        front = "\n\n".join(p for p in (f"# {title}" if title else "",
                    f"## Abstract\n{abstract}" if abstract else "") if p)
        if front:
            emit(front, "title_abstract", "Title / Abstract")
        for asset in figures:
            emit(f"[FIGURE: {asset['id']}]\nCaption: {asset.get('caption', '')}\n"
                 f"Description: {asset.get('description', '')}", "figure", "Figures",
                 figure_id=asset["id"], figure_caption=asset.get("caption", ""))
        for asset in tables:
            emit(f"[TABLE_DATA: {asset['id']}]\nCaption: {asset.get('caption', '')}\n"
                 f"Content:\n{asset.get('description', '')}", "table", "Tables",
                 table_id=asset["id"], table_caption=asset.get("caption", ""))
        for heading, text in self._build_body_sections(sections):
            text, _, _ = self._process_asset_references(text, figures, tables)
            emit(text, "body", heading)
        if meta.get("references_raw"):
            emit(meta["references_raw"], "reference", "References")
        return chunks

    @staticmethod
    def _build_body_sections(
        grobid_sections: List[dict],
    ) -> List[tuple]:
        """Convert GROBID section dicts to (heading, text) tuples for splitting."""
        sections: List[tuple] = []
        for sec in grobid_sections:
            heading = sec.get("heading", "")
            paragraphs = sec.get("paragraphs", [])
            if not paragraphs:
                continue
            text = "\n\n".join(paragraphs)
            if heading:
                level = sec.get("level", 1)
                prefix = "#" * min(level + 1, 6)  # level 1 → ##, level 2 → ###, ...
                text = f"{prefix} {heading}\n\n{text}"
            sections.append((heading, text))
        return sections

    def _process_asset_references(
        self,
        text: str,
        grobid_figures: List[dict],
        grobid_tables: List[dict],
    ) -> tuple:
        """Detect figure/table references and replace with placeholders.

        Returns ``(processed_text, linked_figure_ids, linked_table_ids)``.
        """
        import re

        fig_map = self._build_asset_ref_map(grobid_figures)
        tab_map = self._build_asset_ref_map(grobid_tables)

        linked_figs: List[str] = []
        linked_tabs: List[str] = []

        # Patterns: "Fig. 1", "Figure 1", "Fig.1", "fig1", "Fig 1"
        fig_pattern = re.compile(r'(?:Figure|Fig)\.?\s*(\d+)', re.IGNORECASE)
        tab_pattern = re.compile(r'Table\.?\s*(\d+)', re.IGNORECASE)

        def _replace_fig(match):
            num = int(match.group(1))
            if num in fig_map:
                fid = fig_map[num]
                if fid not in linked_figs:
                    linked_figs.append(fid)
                return f"[FIG_REF: {fid}]"
            return match.group(0)

        def _replace_tab(match):
            num = int(match.group(1))
            if num in tab_map:
                tid = tab_map[num]
                if tid not in linked_tabs:
                    linked_tabs.append(tid)
                return f"[TABLE_REF: {tid}]"
            return match.group(0)

        text = fig_pattern.sub(_replace_fig, text)
        text = tab_pattern.sub(_replace_tab, text)

        return text, linked_figs, linked_tabs

    @staticmethod
    def _build_asset_ref_map(assets: List[dict]) -> dict:
        """Map paper figure/table numbers → GROBID asset IDs.

        Tries to extract the numeric suffix from the GROBID xml:id
        (e.g. ``"fig3"`` → 3), falling back to 1‑based sequential index.
        """
        ref_map: dict = {}
        for idx, asset in enumerate(assets):
            asset_id = asset.get("id", "")
            # 1‑based sequential — GROBID emits figures/tables in document
            # order, so the first asset corresponds to "Figure 1" / "Table 1".
            ref_map[asset.get("number", idx + 1)] = asset_id
        return ref_map

    # ------------------------------------------------------------------
    # Legacy paper chunking (regex / no GROBID)
    # ------------------------------------------------------------------

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

    @staticmethod
    def _split_into_sections(text: str) -> List[tuple[str, str]]:
        import re

        lines = text.splitlines()
        sections: List[tuple[str, str]] = []
        current_title = ""
        current_lines: List[str] = []

        heading_pattern = re.compile(r"^#+\s+(.+)$")
        for line in lines:
            match = heading_pattern.match(line.strip())
            if match:
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                    current_lines = []
                current_title = match.group(1).strip()
                continue
            current_lines.append(line)

        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))

        if not sections:
            sections.append(("", text))

        return sections

    @staticmethod
    def _enrich_paper_chunk_metadata(metadata: dict, text: str) -> None:
        import re

        figure_refs = re.findall(r"\[IMAGE:\s*([^\]]+)\]", text)
        table_refs = re.findall(r"\[TABLE:\s*([^\]]+)\]", text)
        metadata["figure_refs"] = [ref.strip() for ref in figure_refs]
        metadata["table_refs"] = [ref.strip() for ref in table_refs]

        equations = re.findall(r"\$\$(.+?)\$\$", text, re.DOTALL)
        equations += re.findall(r"\\\[(.+?)\\\]", text, re.DOTALL)
        inline_eq = re.findall(r"\$(.+?)\$", text)
        equations.extend(inline_eq)
        equations = [eq.strip() for eq in equations if eq.strip()]
        if equations:
            metadata["equations"] = equations
            metadata["has_equations"] = True

        citation_matches = list(re.finditer(r"\[[0-9,\s]+\]", text))
        contexts = []
        for match in citation_matches:
            start = max(match.start() - 40, 0)
            end = min(match.end() + 40, len(text))
            contexts.append(text[start:end].strip())
        if contexts:
            metadata["citation_contexts"] = contexts
    
    def _generate_chunk_id(self, doc_id: str, index: int, text: str) -> str:
        """Generate unique and deterministic chunk ID.
        
        ID format: {doc_id}_{index:04d}_{content_hash}
        - doc_id: Parent document identifier
        - index: Sequential position (zero-padded to 4 digits)
        - content_hash: First 8 chars of text SHA256 hash
        
        This ensures:
        - Uniqueness: Combination of doc_id + index + content_hash
        - Determinism: Same input always produces same ID
        - Debuggability: Human-readable structure
        
        Args:
            doc_id: Parent document ID
            index: Sequential position of chunk (0-based)
            text: Chunk text content
        
        Returns:
            Unique chunk ID string
        
        Example:
            >>> chunker._generate_chunk_id("doc_123", 0, "Hello world")
            'doc_123_0000_c0535e4b'
        """
        # Compute content hash for uniqueness
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        
        # Format: {doc_id}_{index:04d}_{hash_8chars}
        return f"{doc_id}_{index:04d}_{content_hash}"
    
    def _inherit_metadata(self, document: Document, chunk_index: int, chunk_text: str = "") -> dict:
        """Inherit metadata from document and add chunk-specific fields.
        
        This creates a new metadata dict containing:
        - All fields from document.metadata (copied, not referenced)
        - chunk_index: Sequential position (0-based)
        - source_ref: Reference to parent document ID
        - image_refs: List of image IDs referenced in this chunk (extracted from placeholders)
        
        Note: The document-level 'images' field is intentionally excluded from chunk
        metadata as it would be redundant. Instead, chunk-specific 'image_refs' is
        populated based on [IMAGE: xxx] placeholders found in the chunk text.
        
        Args:
            document: Source document whose metadata to inherit
            chunk_index: Sequential position of this chunk
            chunk_text: The text content of this chunk (used to extract image_refs)
        
        Returns:
            Metadata dict with inherited and chunk-specific fields
        
        Example:
            >>> doc = Document(
            ...     id="doc_123",
            ...     text="Content",
            ...     metadata={"source_path": "file.pdf", "title": "Report"}
            ... )
            >>> metadata = chunker._inherit_metadata(doc, 2, "See [IMAGE: img_001]")
            >>> metadata["source_path"]
            'file.pdf'
            >>> metadata["chunk_index"]
            2
            >>> metadata["source_ref"]
            'doc_123'
            >>> metadata["image_refs"]
            ['img_001']
        """
        import re
        
        # Copy all document metadata (shallow copy is sufficient for primitives)
        chunk_metadata = document.metadata.copy()
        
        # Get document-level images for lookup
        doc_images = document.metadata.get("images", [])
        
        # Remove document-level 'images' field - we'll add chunk-specific images below
        chunk_metadata.pop("images", None)
        
        # Add chunk-specific fields
        chunk_metadata["chunk_index"] = chunk_index
        chunk_metadata["source_ref"] = document.id
        
        # Extract image_refs from chunk text by finding [IMAGE: xxx] placeholders
        image_refs = []
        if chunk_text:
            # Pattern matches [IMAGE: image_id] placeholders
            pattern = r'\[IMAGE:\s*([^\]]+)\]'
            matches = re.findall(pattern, chunk_text)
            image_refs = [m.strip() for m in matches]
        
        chunk_metadata["image_refs"] = image_refs
        
        # Build chunk-specific 'images' list with full metadata for referenced images
        # This is needed by ImageCaptioner to access image paths for Vision API calls
        chunk_images = []
        if image_refs and doc_images:
            image_lookup = {img.get("id"): img for img in doc_images}
            for img_id in image_refs:
                if img_id in image_lookup:
                    chunk_images.append(image_lookup[img_id])
        
        if chunk_images:
            chunk_metadata["images"] = chunk_images
        
        # Try to determine page_num from the first referenced image
        if chunk_images:
            chunk_metadata["page_num"] = chunk_images[0].get("page")
        
        return chunk_metadata
