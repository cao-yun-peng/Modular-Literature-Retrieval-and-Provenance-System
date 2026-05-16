"""
Loader Module.

This package contains document loader components:
- Base loader class
- PDF loader (MarkItDown-based)
- Paper PDF loader (with optional GROBID integration)
- GROBID TEI parser for structured academic metadata
- File integrity checker
"""

from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.pdf_loader import PdfLoader, PaperPdfLoader
from src.libs.loader.file_integrity import FileIntegrityChecker, SQLiteIntegrityChecker

__all__ = [
    "BaseLoader",
    "PdfLoader",
    "PaperPdfLoader",
    "FileIntegrityChecker",
    "SQLiteIntegrityChecker",
]
