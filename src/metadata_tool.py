import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional
import requests
from bs4 import BeautifulSoup
import pdfplumber
from src.models import CourseMetadata

class MetadataExtractor:
    @staticmethod
    def from_pdf(pdf_path: str) -> CourseMetadata:
        """Extracts metadata from PDF's internal metadata fields."""
        with pdfplumber.open(pdf_path) as pdf:
            meta = pdf.metadata
            return CourseMetadata(
                title=meta.get("Title"),
                author=meta.get("Author"),
                description=meta.get("Subject"),
                source="pdf_embedded"
            )

    @staticmethod
    def from_json(json_path: str) -> CourseMetadata:
        """Loads metadata from a JSON file."""
        with open(json_path, "r") as f:
            data = json.load(f)
            return CourseMetadata(**data, source="json_file")

    @staticmethod
    def _extract_section(soup: BeautifulSoup, keywords: list) -> list:
        """Helper to extract list items or text from a section identified by keywords."""
        content = []
        # Find headers or bold text contained in keywords
        tags = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'dt'])
        
        for tag in tags:
            text = tag.get_text().lower()
            if any(k in text for k in keywords):
                # Found a potential section header. Look at siblings.
                for sibling in tag.next_siblings:
                    if sibling.name in ['ul', 'ol']:
                        content.extend([li.get_text().strip() for li in sibling.find_all('li')])
                        break # Assume we found the list
                    elif sibling.name in ['p', 'div']:
                        if sibling.get_text().strip():
                            content.append(sibling.get_text().strip())
                    
                    # Stop if we hit another header
                    if sibling.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                        break
        return list(set(content)) # Deduplicate

    @staticmethod
    def from_url(url: str) -> CourseMetadata:
        """Generic best-effort scraper for course metadata."""
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Basic heuristic extraction
            title = soup.find("title").text if soup.find("title") else "Unknown"
            description = ""
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc:
                description = meta_desc.get("content", "")
            
            # Look for common patterns for outcomes/prerequisites
            prerequisites = MetadataExtractor._extract_section(soup, ["prereq", "requirement", "prior knowledge"])
            outcomes = MetadataExtractor._extract_section(soup, ["outcome", "objective", "goal", "learn", "what you'll learn"])

            return CourseMetadata(
                title=title.strip(),
                description=description.strip(),
                learning_outcomes=outcomes,
                prerequisites=prerequisites,
                source=f"url: {url}"
            )
        except Exception as e:
            print(f"Error scraping URL: {e}", file=sys.stderr)
            return CourseMetadata(source=f"url_failed: {url}")

    @staticmethod
    def from_external_pdf(pdf_path: str) -> CourseMetadata:
        """Extracts text from a syllabus PDF and attempts to identify metadata."""
        # For now, we use a simple text dump. Realistically, this might need 
        # a small LLM call to 'summarize' into metadata, but per constraints 
        # we stick to deterministic extraction or simple heuristics.
        with pdfplumber.open(pdf_path) as pdf:
            first_page_text = pdf.pages[0].extract_text() or ""
            # Heuristic: First line is often title
            lines = [l.strip() for l in first_page_text.split("\n") if l.strip()]
            return CourseMetadata(
                title=lines[0] if lines else "Unknown",
                description=first_page_text[:500],
                source=f"external_pdf: {pdf_path}"
            )

def main():
    parser = argparse.ArgumentParser(description="SemanticFlow Metadata Extractor")
    parser.add_argument("--pdf", type=str, help="Source PDF for embedded metadata")
    parser.add_argument("--url", type=str, help="URL to scrape metadata from")
    parser.add_argument("--json", type=str, help="Manual JSON metadata file")
    parser.add_argument("--syllabus", type=str, help="External syllabus PDF")
    parser.add_argument("--output", type=str, required=True, help="Path to save the normalized JSON")

    args = parser.parse_args()
    
    metadata = None

    if args.json:
        if not os.path.exists(args.json):
            print(f"Error: JSON file not found: {args.json}", file=sys.stderr)
            sys.exit(1)
        metadata = MetadataExtractor.from_json(args.json)
    elif args.url:
        metadata = MetadataExtractor.from_url(args.url)
    elif args.syllabus:
        if not os.path.exists(args.syllabus):
            print(f"Error: Syllabus PDF not found: {args.syllabus}", file=sys.stderr)
            sys.exit(1)
        metadata = MetadataExtractor.from_external_pdf(args.syllabus)
    elif args.pdf:
        if not os.path.exists(args.pdf):
            print(f"Error: PDF file not found: {args.pdf}", file=sys.stderr)
            sys.exit(1)
        metadata = MetadataExtractor.from_pdf(args.pdf)
    else:
        print("Error: No metadata source provided.", file=sys.stderr)
        sys.exit(1)

    if metadata:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(metadata.model_dump_json(indent=2))
        print(f"[+] Metadata saved to: {output_path}")

if __name__ == "__main__":
    main()
