import json
import pytest
from pathlib import Path
from src.metadata import MetadataIngestor, CourseMetadata  # metadata.py's own CourseMetadata


# ============================================================================
# Happy path: sibling JSON file
# ============================================================================

def test_external_json_full_fields(tmp_path):
    """All supported fields from a sibling JSON file are parsed correctly."""
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()

    data = {
        "title": "Advanced AI",
        "author": "Dr. Smith",
        "prerequisites": ["Python", "Math"],
        "learning_outcomes": ["Understand neural networks", "Apply backpropagation"],
        "target_audience": "Graduate students",
        "subject": "Artificial Intelligence",
        "description": "A comprehensive AI course.",
        "level": "Advanced",
    }
    (tmp_path / "course.json").write_text(json.dumps(data))

    metadata = MetadataIngestor(pdf_path).ingest()

    assert metadata.title == "Advanced AI"
    assert metadata.author == "Dr. Smith"
    assert "Python" in metadata.prerequisites
    assert "Math" in metadata.prerequisites
    assert "Understand neural networks" in metadata.learning_outcomes
    assert metadata.target_audience == "Graduate students"
    assert metadata.level == "Advanced"


def test_external_json_missing_optional_fields_use_defaults(tmp_path):
    """Partial JSON with only title/author → other fields fall back to 'Unknown' (not None).

    metadata.py's CourseMetadata uses str defaults of 'Unknown', not Optional[str]=None.
    This is intentional: downstream LLM prompts expect string values, not None.
    """
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()
    (tmp_path / "course.json").write_text(json.dumps({"title": "Minimal Course", "author": "Someone"}))

    metadata = MetadataIngestor(pdf_path).ingest()

    assert metadata.title == "Minimal Course"
    assert metadata.prerequisites == []      # list fields default to empty list
    assert metadata.learning_outcomes == []  # list fields default to empty list
    assert metadata.description == "Unknown" # scalar string fields default to 'Unknown'


# ============================================================================
# Fallback: no sibling file, autoscan from PDF
# ============================================================================

def test_autoscan_fallback_when_no_sibling_file(tmp_path):
    """With no sibling JSON/TXT/HTML, ingest() falls back to autoscan (PDF text parse).

    We use a non-existent PDF — pdfplumber will fail gracefully returning a CourseMetadata
    with all-Unknown fields except `source` which is set to the PDF filename.
    """
    pdf_path = tmp_path / "missing.pdf"
    # Do NOT create the file — triggers graceful failure in autoscan
    ingestor = MetadataIngestor(pdf_path)
    metadata = ingestor.ingest()
    # Must return metadata.py's local CourseMetadata (not src.models.CourseMetadata)
    assert isinstance(metadata, CourseMetadata)
    # Source is set to the pdf filename even on extraction failure
    assert metadata.source == "missing.pdf"


# ============================================================================
# Explicit metadata source flag
# ============================================================================

def test_explicit_metadata_source_overrides_sibling(tmp_path):
    """When metadata_source is provided explicitly, sibling JSON is ignored."""
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()

    # Sibling file that would be picked up by autoscan
    (tmp_path / "course.json").write_text(json.dumps({"title": "Sibling Title"}))

    # Explicit metadata file with a different title
    explicit_path = tmp_path / "override.json"
    explicit_path.write_text(json.dumps({"title": "Explicit Title", "author": "Override Author"}))

    metadata = MetadataIngestor(pdf_path, metadata_source=explicit_path).ingest()

    assert metadata.title == "Explicit Title"
    assert metadata.author == "Override Author"


# ============================================================================
# Source field is always populated
# ============================================================================

def test_source_field_set_to_pdf_filename(tmp_path):
    """The `source` field is populated when metadata is extracted from a PDF.

    When reading from a sibling JSON, source comes from whatever is in that JSON.
    When falling back to PDF parsing, source is set to the PDF filename.
    We test the PDF fallback path to verify source is set.
    """
    pdf_path = tmp_path / "mybook.pdf"
    # No sibling file → autoscan → PDF extraction fallback → source = pdf filename
    metadata = MetadataIngestor(pdf_path).ingest()
    assert metadata.source == "mybook.pdf"
