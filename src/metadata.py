import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import pdfplumber
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class CourseMetadata(BaseModel):
    title: str = "Unknown"
    author: str = "Unknown"
    target_audience: str = "Unknown"
    subject: str = "Unknown"
    source: str = "Unknown"
    description: str = "Unknown"
    prerequisites: List[str] = Field(default_factory=list)
    learning_outcomes: List[str] = Field(default_factory=list)

class MetadataIngestor:
    """Handles extraction of metadata from external files or the PDF itself."""
    
    def __init__(self, pdf_path: Path):
        self.pdf_path = Path(pdf_path)
        self.base_name = self.pdf_path.stem
        self.dir_path = self.pdf_path.parent

    def ingest(self) -> CourseMetadata:
        """Attempt to read from external files, fallback to PDF extraction."""
        # Check JSON
        json_path = self.dir_path / f"{self.base_name}.json"
        if json_path.exists():
            logger.info(f"Found external metadata JSON: {json_path}")
            return self._parse_json(json_path)

        # Check txt
        txt_path = self.dir_path / f"{self.base_name}.txt"
        if txt_path.exists():
            logger.info(f"Found external metadata TXT: {txt_path}")
            return self._parse_txt(txt_path)
            
        # Check html
        html_path = self.dir_path / f"{self.base_name}.html"
        if html_path.exists():
            logger.info(f"Found external metadata HTML: {html_path}")
            return self._parse_html(html_path)

        logger.info(f"No external metadata found for '{self.base_name}'. Falling back to PDF extraction.")
        return self._extract_metadata_from_pdf()

    def _parse_json(self, path: Path) -> CourseMetadata:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Filter to match CourseMetadata fields
            valid_fields = CourseMetadata.model_fields.keys()
            filtered_data = {k: v for k, v in data.items() if k in valid_fields}
            return CourseMetadata(**filtered_data)
        except Exception as e:
            logger.error(f"Failed to parse JSON {path}: {e}")
            return CourseMetadata(source=self.pdf_path.name)

    def _parse_txt(self, path: Path) -> CourseMetadata:
        data = {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if ':' in line:
                        key, val = line.split(':', 1)
                        clean_key = key.strip().lower().replace(' ', '_')
                        if clean_key in ['prerequisites', 'learning_outcomes']:
                            data[clean_key] = [v.strip() for v in val.split(',')]
                        else:
                            data[clean_key] = val.strip()
            valid_fields = CourseMetadata.model_fields.keys()
            filtered_data = {k: v for k, v in data.items() if k in valid_fields}
            return CourseMetadata(**filtered_data)
        except Exception as e:
            logger.error(f"Failed to parse TXT {path}: {e}")
            return CourseMetadata(source=self.pdf_path.name)

    def _parse_html(self, path: Path) -> CourseMetadata:
        # Simplistic parsing, assume Title might be in there, but mostly fallback
        logger.warning(f"HTML metadata extraction is basic. Using default for {path.name}.")
        return CourseMetadata(source=self.pdf_path.name)

    def _extract_metadata_from_pdf(self) -> CourseMetadata:
        """Extract basic metadata from the PDF properties and first pages."""
        metadata = CourseMetadata(source=self.pdf_path.name)
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                # Get PDF document info dictionary
                doc_info = pdf.metadata or {}
                
                # PyPDF2/pdfplumber metadata keys are typically Title, Author, Subject
                title = doc_info.get('Title')
                if title and isinstance(title, str) and title.strip():
                    metadata.title = title
                else:
                    metadata.title = self.pdf_path.stem.replace('_', ' ').replace('-', ' ').title()
                    
                author = doc_info.get('Author')
                if author and isinstance(author, str) and author.strip():
                    metadata.author = author
                    
                subject = doc_info.get('Subject')
                if subject and isinstance(subject, str) and subject.strip():
                    metadata.subject = subject
                    
        except Exception as e:
            logger.error(f"Failed to extract metadata from PDF {self.pdf_path}: {e}")
            
        return metadata
