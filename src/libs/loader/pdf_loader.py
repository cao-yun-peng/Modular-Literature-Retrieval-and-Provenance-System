"""PDF Loader implementation using MarkItDown.

This module implements PDF parsing with image extraction support,
converting PDFs to standardized Markdown format with image placeholders.

Features:
- Text extraction and Markdown conversion via MarkItDown
- Image extraction and storage
- Image placeholder insertion with metadata tracking
- Graceful degradation if image extraction fails
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

from PIL import Image
import io

from src.core.types import Document
from src.libs.loader.base_loader import BaseLoader

logger = logging.getLogger(__name__)


class PdfLoader(BaseLoader):
    """PDF Loader using MarkItDown for text extraction and Markdown conversion.
    
    This loader:
    1. Extracts text from PDF and converts to Markdown
    2. Extracts images and saves to data/images/{doc_hash}/
    3. Inserts image placeholders in the format [IMAGE: {image_id}]
    4. Records image metadata in Document.metadata.images
    
    Configuration:
        extract_images: Enable/disable image extraction (default: True)
        image_storage_dir: Base directory for image storage (default: data/images)
    
    Graceful Degradation:
        If image extraction fails, logs warning and continues with text-only parsing.
    """
    
    def __init__(
        self,
        extract_images: bool = True,
        image_storage_dir: str | Path = "data/images"
    ):
        """Initialize PDF Loader.
        
        Args:
            extract_images: Whether to extract images from PDFs.
            image_storage_dir: Base directory for storing extracted images.
        """
        if not MARKITDOWN_AVAILABLE:
            raise ImportError(
                "MarkItDown is required for PdfLoader. "
                "Install with: pip install markitdown"
            )
        
        self.extract_images = extract_images
        self.image_storage_dir = Path(image_storage_dir)
        self._markitdown = MarkItDown()
    
    def load(self, file_path: str | Path) -> Document:
        """Load and parse a PDF file.
        
        Args:
            file_path: Path to the PDF file.
            
        Returns:
            Document with Markdown text and metadata.
            
        Raises:
            FileNotFoundError: If the PDF file doesn't exist.
            ValueError: If the file is not a valid PDF.
            RuntimeError: If parsing fails critically.
        """
        # Validate file
        path = self._validate_file(file_path)
        if path.suffix.lower() != '.pdf':
            raise ValueError(f"File is not a PDF: {path}")
        
        # Compute document hash for unique ID and image directory
        doc_hash = self._compute_file_hash(path)
        doc_id = f"doc_{doc_hash[:16]}"
        
        # Parse PDF with MarkItDown
        try:
            result = self._markitdown.convert(str(path))
            text_content = result.text_content if hasattr(result, 'text_content') else str(result)
        except Exception as e:
            logger.error(f"Failed to parse PDF {path}: {e}")
            raise RuntimeError(f"PDF parsing failed: {e}") from e
        
        # Initialize metadata
        metadata: Dict[str, Any] = {
            "source_path": str(path),
            "doc_type": "pdf",
            "doc_hash": doc_hash,
        }
        
        # Extract title from first heading if available
        title = self._extract_title(text_content)
        if title:
            metadata["title"] = title
        
        # Handle image extraction (with graceful degradation)
        if self.extract_images:
            try:
                text_content, images_metadata = self._extract_and_process_images(
                    path, text_content, doc_hash
                )
                if images_metadata:
                    metadata["images"] = images_metadata
            except Exception as e:
                logger.warning(
                    f"Image extraction failed for {path}, continuing with text-only: {e}"
                )
        
        return Document(
            id=doc_id,
            text=text_content,
            metadata=metadata
        )
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file content.
        
        Args:
            file_path: Path to file.
            
        Returns:
            Hex string of SHA256 hash.
        """
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def _extract_title(self, text: str) -> Optional[str]:
        """Extract title from first Markdown heading or first non-empty line.
        
        Args:
            text: Markdown text content.
            
        Returns:
            Title string if found, None otherwise.
        """
        lines = text.split('\n')
        
        # First try to find a markdown heading
        for line in lines[:20]:  # Check first 20 lines
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()
        
        # Fallback: use first non-empty line as title
        for line in lines[:10]:
            line = line.strip()
            if line and len(line) > 0:
                return line
        
        return None
    
    def _extract_and_process_images(
        self,
        pdf_path: Path,
        text_content: str,
        doc_hash: str
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Extract images from PDF and insert placeholders.
        
        Uses PyMuPDF to extract images, save them to disk, and insert
        placeholders in the text content.
        
        Args:
            pdf_path: Path to PDF file.
            text_content: Extracted text content.
            doc_hash: Document hash for image directory.
            
        Returns:
            Tuple of (modified_text, images_metadata_list)
        """
        if not self.extract_images:
            logger.debug(f"Image extraction disabled for {pdf_path}")
            return text_content, []
        
        if not PYMUPDF_AVAILABLE:
            logger.warning(f"PyMuPDF not available, skipping image extraction for {pdf_path}")
            return text_content, []
        
        images_metadata = []
        modified_text = text_content
        
        try:
            # Create image storage directory
            image_dir = self.image_storage_dir / doc_hash
            image_dir.mkdir(parents=True, exist_ok=True)
            
            # Open PDF with PyMuPDF
            doc = fitz.open(pdf_path)
            
            # Track text offset for placeholder insertion
            text_offset = 0
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                
                for img_index, img_info in enumerate(image_list):
                    try:
                        # Extract image
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"] #这是 PyMuPDF 提供的方法，给定一个 xref 编号，它会解析对应的 PDF 对象，
                          # 提取出图像的二进制数据、格式信息等，并以字典形式返回。返回的 base_image 字典包含多个键值对，例如：
                            #"ext": "png",          # 图像的扩展名（格式）
                            #"width": 800,          # 图像宽度
                            #"height": 600,         # 图像高度
                            #"colorspace": 3,       # 颜色空间
                            #"image": b'...'        # 图像的二进制数据（可直接写入文件）
                        # Generate image ID and filename
                        image_id = self._generate_image_id(doc_hash, page_num + 1, img_index + 1)
                        image_filename = f"{image_id}.{image_ext}"
                        image_path = image_dir / image_filename
                        
                        # Save image
                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)
                        
                        # Get image dimensions
                        try:
                            img = Image.open(io.BytesIO(image_bytes))#将内存中的图像字节数据包装成一个类似文件的对象，这样 PIL.Image.open() 可以直接从内存中读取图像，而无需先保存到磁盘再读取，提高了效率。
                            width, height = img.size
                        except Exception:
                            width, height = 0, 0
                        
                        # Create placeholder
                        placeholder = f"[IMAGE: {image_id}]"
                        
                        # Insert placeholder at end of current page's content
                        # (simplified - in production, you'd parse page boundaries)
                        insert_position = len(modified_text)
                        modified_text += f"\n{placeholder}\n"
                        
                        # Convert path to be relative to project root or absolute
                        try:
                            relative_path = image_path.relative_to(Path.cwd())
                        except ValueError:
                            # If not in cwd, use absolute path
                            relative_path = image_path.absolute()
                        
                        # Record metadata
                        image_metadata = {
                            "id": image_id,
                            "path": str(relative_path),
                            "page": page_num + 1,
                            "text_offset": insert_position + 1,  # +1 for newline
                            "text_length": len(placeholder),
                            "position": {
                                "width": width,
                                "height": height,
                                "page": page_num + 1,
                                "index": img_index
                            }
                        }
                        images_metadata.append(image_metadata)
                        
                        logger.debug(f"Extracted image {image_id} from page {page_num + 1}")
                        
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num + 1}: {e}")
                        continue
            
            doc.close()
            
            if images_metadata:
                logger.info(f"Extracted {len(images_metadata)} images from {pdf_path}")
            else:
                logger.debug(f"No images found in {pdf_path}")
            
            return modified_text, images_metadata
            
        except Exception as e:
            logger.warning(f"Image extraction failed for {pdf_path}: {e}")
            # Graceful degradation: return original text without images
            return text_content, []
    
    @staticmethod
    def _generate_image_id(doc_hash: str, page: int, sequence: int) -> str:
        """Generate unique image ID.
        
        Args:
            doc_hash: Document hash.
            page: Page number (0-based).
            sequence: Image sequence on page (0-based).
            
        Returns:
            Unique image ID string.
        """
        return f"{doc_hash[:8]}_{page}_{sequence}"


class PaperPdfLoader(PdfLoader):
    """Paper-oriented PDF loader with additional academic metadata extraction.

    When GROBID is available, structured TEI XML parsing provides high-quality
    extraction of title, authors, abstract, sections, figures, and tables.
    Falls back gracefully to regex-based heuristics when GROBID is unavailable.
    """

    def __init__(
        self,
        extract_images: bool = True,
        image_storage_dir: str | Path = "data/images",
        table_storage_dir: str | Path = "data/tables",
        collection: str = "default",
        extract_tables: bool = True,
        use_grobid: bool = True,
        grobid_url: str = "http://localhost:8070",
    ) -> None:
        super().__init__(extract_images=extract_images, image_storage_dir=image_storage_dir)
        self.table_storage_dir = Path(table_storage_dir)
        self.collection = collection
        self.extract_tables = extract_tables
        self.use_grobid = use_grobid
        self.grobid_url = grobid_url

    def load(self, file_path: str | Path) -> Document:
        """Load PDF and enrich with paper-specific metadata.

        Uses GROBID for structured metadata extraction when available,
        falling back to regex heuristics otherwise.
        """
        base_doc = super().load(file_path)
        text = base_doc.text
        metadata = base_doc.metadata.copy()
        metadata["doc_type"] = "paper"
        metadata["paper_mode"] = True

        # ---- GROBID path ---------------------------------------------------
        grobid_paper = None
        if self.use_grobid:
            try:
                from src.libs.loader.grobid_parser import GrobidClient, GrobidTEIParser
                client = GrobidClient(base_url=self.grobid_url)
                if client.is_alive():
                    tei_xml = client.pdf_to_tei(str(file_path))
                    parser = GrobidTEIParser(tei_xml)
                    grobid_paper = parser.parse()
                    logger.info(
                        "GROBID extracted: title=%r, %d authors, %d sections",
                        grobid_paper.title,
                        len(grobid_paper.authors),
                        len(grobid_paper.sections),
                    )
                else:
                    logger.warning("GROBID service unavailable, using regex fallback")
            except Exception as e:
                logger.warning("GROBID parsing failed (%s), using regex fallback", e)

        if grobid_paper:
            paper_meta = self._grobid_to_metadata(grobid_paper)
            # Supplement with regex for fields GROBID does not cover
            regex_meta = self._extract_paper_metadata(text)
            for key in ("keywords", "references_raw", "bib_entries", "equations",
                        "year", "venue"):
                if regex_meta.get(key):
                    paper_meta.setdefault(key, regex_meta[key])
            for key in ("doi", "arxiv_id"):
                if regex_meta.get(key) and not paper_meta.get(key):
                    paper_meta[key] = regex_meta[key]
        else:
            paper_meta = self._extract_paper_metadata(text)

        metadata.update({k: v for k, v in paper_meta.items() if v is not None})

        if self.extract_tables:
            text, tables = self._extract_tables(text, metadata.get("source_path"))
            if tables:
                metadata["tables"] = tables

        return Document(id=base_doc.id, text=text, metadata=metadata)

    def _grobid_to_metadata(self, paper: Any) -> Dict[str, Any]:
        """Convert GROBID-parsed Paper to metadata dict compatible with downstream.

        The output dict uses the same keys as ``_extract_paper_metadata`` so
        that consumers (DocumentChunker, MetadataEnricher, tools) work
        unchanged regardless of whether GROBID or regex produced the metadata.
        """
        from dataclasses import asdict

        meta: Dict[str, Any] = {}

        if paper.title:
            meta["title"] = paper.title
        if paper.authors:
            meta["authors"] = paper.authors
        if paper.abstract:
            meta["abstract"] = paper.abstract
        if paper.doi:
            meta["doi"] = paper.doi
        if paper.arxiv_id:
            meta["arxiv_id"] = paper.arxiv_id

        if paper.sections:
            meta["sections"] = [
                {"title": s.heading, "level": s.level}
                for s in paper.sections
                if s.heading
            ]
            meta["toc"] = [s.heading for s in paper.sections if s.heading]
            meta["grobid_sections"] = [
                {
                    "heading": s.heading,
                    "paragraphs": s.paragraphs,
                    "level": s.level,
                }
                for s in paper.sections
            ]

        if paper.figures:
            meta["grobid_figures"] = [asdict(f) for f in paper.figures]

        if paper.tables:
            meta["grobid_tables"] = [asdict(t) for t in paper.tables]

        return meta

    def _extract_paper_metadata(self, text: str) -> Dict[str, Any]:
        import re

        metadata: Dict[str, Any] = {}

        title = self._extract_title(text)
        if title:
            metadata["title"] = title

        authors = self._extract_authors(text, title)
        if authors:
            metadata["authors"] = authors

        abstract = self._extract_section_text(text, "abstract")
        if abstract:
            metadata["abstract"] = abstract

        keywords = self._extract_keywords(text)
        if keywords:
            metadata["keywords"] = keywords

        sections = self._extract_sections(text)
        if sections:
            metadata["sections"] = sections
            metadata["toc"] = [s.get("title") for s in sections if s.get("title")]

        references_raw = self._extract_references(text)
        if references_raw:
            metadata["references_raw"] = references_raw
            metadata["bib_entries"] = self._parse_bib_entries(references_raw)

        doi = self._extract_doi(text)
        if doi:
            metadata["doi"] = doi

        arxiv_id = self._extract_arxiv_id(text)
        if arxiv_id:
            metadata["arxiv_id"] = arxiv_id

        venue_year = self._extract_venue_year(text)
        if venue_year:
            metadata.update(venue_year)

        equations = self._extract_equations(text)
        if equations:
            metadata["equations"] = equations

        return metadata

    def _extract_tables(self, text: str, source_path: Optional[str]) -> tuple[str, List[Dict[str, Any]]]:
        try:
            from src.ingestion.transform.table_extractor import TableExtractor
        except Exception:
            return text, []

        extractor = TableExtractor(self.table_storage_dir)
        tables, modified_text = extractor.extract_tables_from_text(text, source_path=source_path)
        if tables:
            extractor.save_tables(tables, collection=self.collection)
        return modified_text, tables

    @staticmethod
    def _extract_authors(text: str, title: Optional[str]) -> List[str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        if title:
            for idx, line in enumerate(lines[:10]):
                if line == title and idx + 1 < len(lines):
                    return [a.strip() for a in lines[idx + 1].split(",") if a.strip()]

        # Fallback: second non-empty line
        if len(lines) > 1:
            return [a.strip() for a in lines[1].split(",") if a.strip()]
        return []

    @staticmethod
    def _extract_section_text(text: str, heading: str) -> Optional[str]:
        import re

        pattern = re.compile(rf"^#+\s*{re.escape(heading)}\s*$", re.IGNORECASE | re.MULTILINE)
        match = pattern.search(text)
        if not match:
            return None

        start = match.end()
        remainder = text[start:]
        next_heading = re.search(r"^#+\s+.+$", remainder, re.MULTILINE)
        end = start + next_heading.start() if next_heading else len(text)
        return text[start:end].strip()

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        import re

        match = re.search(r"^\s*keywords\s*[:：]\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        if not match:
            return []
        raw = match.group(1)
        parts = [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]
        return parts

    @staticmethod
    def _extract_sections(text: str) -> List[Dict[str, Any]]:
        import re

        sections: List[Dict[str, Any]] = []
        for line in text.splitlines():
            match = re.match(r"^(#+)\s+(.+)$", line.strip())
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                sections.append({"title": title, "level": level})
        return sections

    @staticmethod
    def _extract_references(text: str) -> Optional[str]:
        import re

        for heading in ("references", "bibliography"):
            section_text = PaperPdfLoader._extract_section_text(text, heading)
            if section_text:
                return section_text
        return None

    @staticmethod
    def _parse_bib_entries(raw: str) -> List[Dict[str, Any]]:
        import re

        entries: List[Dict[str, Any]] = []
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        buffer = ""
        for line in lines:
            if re.match(r"^\[\d+\]|^\d+\.", line):
                if buffer:
                    entries.append(PaperPdfLoader._parse_bib_entry(buffer))
                buffer = line
            else:
                buffer = f"{buffer} {line}".strip()
        if buffer:
            entries.append(PaperPdfLoader._parse_bib_entry(buffer))
        return entries

    @staticmethod
    def _parse_bib_entry(raw: str) -> Dict[str, Any]:
        doi = PaperPdfLoader._extract_doi(raw)
        title_match = None
        if "\"" in raw:
            import re
            title_match = re.search(r"\"([^\"]+)\"", raw)
        return {
            "raw": raw,
            "doi": doi,
            "title": title_match.group(1) if title_match else None,
        }

    @staticmethod
    def _extract_doi(text: str) -> Optional[str]:
        import re

        match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.IGNORECASE)
        return match.group(0).rstrip(".,;:") if match else None

    @staticmethod
    def _extract_arxiv_id(text: str) -> Optional[str]:
        import re

        match = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", text, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _extract_venue_year(text: str) -> Dict[str, Any]:
        import re

        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        venue_match = re.search(r"\b(Proceedings|Conference|Journal)\b.+", text)
        result: Dict[str, Any] = {}
        if year_match:
            result["year"] = int(year_match.group(0))
        if venue_match:
            result["venue"] = venue_match.group(0).strip()
        return result

    @staticmethod
    def _extract_equations(text: str) -> List[str]:
        import re

        equations: List[str] = []
        # Extract display math first, then remove those regions before inline matching
        equations.extend(re.findall(r"\$\$(.+?)\$\$", text, re.DOTALL))
        equations.extend(re.findall(r"\\\[(.+?)\\\]", text, re.DOTALL))
        # Remove display-math regions to avoid matching inline $ inside $$...$$
        cleaned = re.sub(r"\$\$.+?\$\$", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"\\\[.+?\\\]", "", cleaned, flags=re.DOTALL)
        equations.extend(re.findall(r"\$(.+?)\$", cleaned))
        return [eq.strip() for eq in equations if eq.strip()]
