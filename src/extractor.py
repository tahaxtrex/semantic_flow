import pdfplumber
import logging
from typing import List, Dict, Any
from pathlib import Path

class PDFExtractor:
    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def extract_full_text(self) -> List[Dict[str, Any]]:
        """
        Extracts text and basic metadata page by page.
        Returns a list of dictionaries with page_number and text.
        """
        pages_content = []
        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                # We prioritize text and table extraction
                text = page.extract_text() or ""
                tables = page.extract_tables()
                
                # Simple table to string conversion for low-fidelity context
                table_text = ""
                if tables:
                    for table in tables:
                        for row in table:
                            table_text += " | ".join([str(cell) if cell else "" for cell in row]) + "\n"
                
                full_page_text = text + "\n" + table_text
                pages_content.append({
                    "page_number": i + 1,
                    "text": full_page_text.strip()
                })
        return pages_content

    def get_bookmarks(self) -> List[Dict[str, Any]]:
        """
        Extracts the Table of Contents (bookmarks) if available.
        """
        bookmarks = []
        with pdfplumber.open(self.pdf_path) as pdf:
            # pdfplumber's toc is usually available in pdf.doc.get_outlines() 
            # or through other attributes depending on version. 
            # We'll use a safe extraction of internal outlines.
            try:
                outlines = pdf.doc.get_outlines()
                for outline in outlines:
                    # outline format: (level, title, dest, action, se)
                    if hasattr(outline, 'title') and hasattr(outline, 'page_index'):
                        bookmarks.append({
                            "level": getattr(outline, 'level', 1),
                            "title": outline.title,
                            "page_index": outline.page_index
                        })
            except Exception:
                # Fallback: empty list if bookmarks are missing or non-standard
                pass
        return bookmarks

    def get_metadata(self) -> Dict[str, Any]:
        with pdfplumber.open(self.pdf_path) as pdf:
            return pdf.metadata
