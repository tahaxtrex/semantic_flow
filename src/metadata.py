import json
import logging
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional
import pdfplumber
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Audience / prerequisite / outcome marker patterns for Issue 7 extraction
_AUDIENCE_PATTERNS = [
    re.compile(r'(?:intended for|suitable for|designed for|aimed at|for students|for\s+\w+\s+(?:students|developers|programmers))\s+([^.]{10,120})', re.IGNORECASE),
]
_PREREQ_PATTERNS = [
    re.compile(r'(?:prerequisite[s]?|prior knowledge|assumes|requires)\s*[:\-]?\s*([^.]{10,120})', re.IGNORECASE),
]
_OUTCOME_PATTERNS = [
    re.compile(r'(?:you will learn|by the end|after completing|learning objective[s]?|upon completion|objectives?)\s*[:\-]?\s*([^.]{10,200})', re.IGNORECASE),
]


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
    """Handles extraction of metadata from external files, URLs, or the PDF itself.
    
    Supports two modes:
      1. Explicit source (--metadata flag): extracts from the given file/URL.
      2. Auto-scan (default): checks for sibling .json/.txt/.html files matching
         the PDF basename, then falls back to native PDF extraction.
    
    Can also be run standalone to produce a reviewable JSON file:
        python3 -m src.metadata --pdf <path> --output <path.json>
    """

    def __init__(self, course_pdf_path: Path, metadata_source: Optional[str] = None):
        self.metadata_source = str(metadata_source) if metadata_source else None
        self.course_pdf_path = Path(course_pdf_path)
        self.base_name = self.course_pdf_path.stem
        self.dir_path = self.course_pdf_path.parent

    def ingest(self) -> CourseMetadata:
        """Extract metadata from the explicit source if provided, otherwise auto-scan."""
        # --- Explicit source path (when --metadata is provided) ---
        if self.metadata_source:
            logger.info(f"Extracting metadata from explicit source: {self.metadata_source}")

            # URL
            if self.metadata_source.startswith("http://") or self.metadata_source.startswith("https://"):
                return self._parse_url()

            path = Path(self.metadata_source)
            if not path.exists():
                logger.warning(f"Metadata file {path} not found. Using default.")
                return CourseMetadata(source=self.course_pdf_path.name)

            ext = path.suffix.lower()
            if ext == '.json':
                return self._parse_json(path)
            elif ext == '.txt':
                return self._parse_txt(path)
            elif ext == '.html':
                return self._parse_html(path)
            elif ext == '.pdf':
                return self._extract_metadata_from_pdf(path)
            else:
                logger.warning(f"Unsupported metadata extension {ext}. Treating as PDF.")
                return self._extract_metadata_from_pdf(path)

        # --- Auto-scan fallback (original behavior when no --metadata flag) ---
        json_path = self.dir_path / f"{self.base_name}.json"
        if json_path.exists():
            logger.info(f"Found external metadata JSON: {json_path}")
            return self._parse_json(json_path)

        txt_path = self.dir_path / f"{self.base_name}.txt"
        if txt_path.exists():
            logger.info(f"Found external metadata TXT: {txt_path}")
            return self._parse_txt(txt_path)

        html_path = self.dir_path / f"{self.base_name}.html"
        if html_path.exists():
            logger.info(f"Found external metadata HTML: {html_path}")
            return self._parse_html(html_path)

        logger.info(f"No external metadata found for '{self.base_name}'. Falling back to PDF extraction.")
        return self._extract_metadata_from_pdf(self.course_pdf_path)

    # ── Parsers ──────────────────────────────────────────────────────────

    def _parse_url(self) -> CourseMetadata:
        try:
            req = urllib.request.Request(self.metadata_source, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8', errors='ignore')

                if self.metadata_source.endswith('.json') or content.strip().startswith('{'):
                    try:
                        data = json.loads(content)
                        valid_fields = CourseMetadata.model_fields.keys()
                        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
                        return CourseMetadata(**filtered_data)
                    except Exception:
                        pass

                metadata = CourseMetadata(source=self.metadata_source)
                self._infer_from_text(metadata, content)
                return metadata
        except Exception as e:
            logger.error(f"Failed to fetch metadata from URL {self.metadata_source}: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_json(self, path: Path) -> CourseMetadata:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            valid_fields = CourseMetadata.model_fields.keys()
            filtered_data = {k: v for k, v in data.items() if k in valid_fields}
            return CourseMetadata(**filtered_data)
        except Exception as e:
            logger.error(f"Failed to parse JSON {path}: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

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
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_html(self, path: Path) -> CourseMetadata:
        logger.warning(f"HTML metadata extraction is basic. Using default for {path.name}.")
        return CourseMetadata(source=self.course_pdf_path.name)

    def _extract_metadata_from_pdf(self, pdf_path: Path) -> CourseMetadata:
        """Extract metadata from PDF properties and infer audience/prerequisites/outcomes
        from the first 15 body pages or 5000 words (critic.md Issue 7)."""
        metadata = CourseMetadata(source=pdf_path.name)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                doc_info = pdf.metadata or {}

                title = doc_info.get('Title')
                if title and isinstance(title, str) and title.strip():
                    metadata.title = title
                else:
                    metadata.title = pdf_path.stem.replace('_', ' ').replace('-', ' ').title()

                author = doc_info.get('Author')
                if author and isinstance(author, str) and author.strip():
                    metadata.author = author

                subject = doc_info.get('Subject')
                if subject and isinstance(subject, str) and subject.strip():
                    metadata.subject = subject

                # Text-based inference from first 15 pages or 5000 words
                intro_text = ""
                scan_pages = min(15, len(pdf.pages))
                word_count = 0
                for page in pdf.pages[:scan_pages]:
                    H = float(page.height)
                    W = float(page.width)
                    body = page.within_bbox((0, H * 0.10, W, H * 0.92))
                    words = body.extract_words()
                    page_text = " ".join(w['text'] for w in words)
                    intro_text += page_text + "\n"

                    word_count += len(page_text.split())
                    if word_count > 5000:
                        break

                if intro_text.strip():
                    self._infer_from_text(metadata, intro_text)

        except Exception as e:
            logger.error(f"Failed to extract metadata from PDF {pdf_path}: {e}")

        return metadata

    def _infer_from_text(self, metadata: CourseMetadata, text: str) -> None:
        """Apply pattern matching against intro text to populate missing metadata fields."""
        # Target audience
        if metadata.target_audience == "Unknown":
            for pat in _AUDIENCE_PATTERNS:
                m = pat.search(text)
                if m:
                    metadata.target_audience = m.group(1).strip().rstrip('.').strip()
                    logger.info(f"Inferred target_audience: {metadata.target_audience}")
                    break

        # Prerequisites
        if not metadata.prerequisites:
            found = []
            for pat in _PREREQ_PATTERNS:
                for m in pat.finditer(text):
                    item = m.group(1).strip().rstrip('.').strip()
                    if item and item not in found:
                        found.append(item)
            if found:
                metadata.prerequisites = found
                logger.info(f"Inferred prerequisites: {found}")

        # Learning outcomes
        if not metadata.learning_outcomes:
            found = []
            for pat in _OUTCOME_PATTERNS:
                for m in pat.finditer(text):
                    item = m.group(1).strip().rstrip('.').strip()
                    if item and item not in found:
                        found.append(item)
            if found:
                metadata.learning_outcomes = found
                logger.info(f"Inferred learning_outcomes: {found}")


# ── Standalone CLI ───────────────────────────────────────────────────────
# Usage:
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --output data/courses/Dsa_metadata.json
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --metadata https://example.com/meta.json --output out.json

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Standalone Metadata Extractor — extract course metadata to a reviewable JSON file."
    )
    parser.add_argument("--pdf", type=str, required=True, help="Path to the course PDF.")
    parser.add_argument("--metadata", type=str, default=None,
                        help="Optional explicit path or URL to an external metadata source.")
    parser.add_argument("--output", type=str, required=True,
                        help="Path to save the extracted JSON metadata for review.")

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error(f"PDF file not found: {pdf_path}")
        sys.exit(1)

    ingestor = MetadataIngestor(course_pdf_path=pdf_path, metadata_source=args.metadata)
    metadata = ingestor.ingest()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(metadata.model_dump_json(indent=2))

    print(f"\n✅ Metadata saved to: {output_path}")
    print("   Review and edit the JSON, then pass it to the evaluator:")
    print(f"   python3 -m src.main --input <courses_dir> --output <output_dir> --metadata {output_path}")
