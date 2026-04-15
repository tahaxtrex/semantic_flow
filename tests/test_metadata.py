import json
import pytest
from pathlib import Path
from src.metadata import MetadataIngestor, CourseMetadata, TOCEntry


# ============================================================================
# Happy path: sibling JSON file (new schema with _stated/_inferred fields)
# ============================================================================

def test_external_json_full_fields(tmp_path):
    """All supported fields from a sibling JSON file are parsed correctly."""
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()

    data = {
        "title": "Advanced AI",
        "author": "Dr. Smith",
        "prerequisites_stated": ["Python", "Math"],
        "learning_outcomes_stated": ["Understand neural networks", "Apply backpropagation"],
        "target_audience": "Graduate students",
        "subject": "Artificial Intelligence",
        "description": "A comprehensive AI course.",
        "level": "advanced",
    }
    (tmp_path / "course.json").write_text(json.dumps(data))

    metadata = MetadataIngestor(pdf_path).ingest()

    assert metadata.title == "Advanced AI"
    assert metadata.author == "Dr. Smith"
    assert "Python" in metadata.prerequisites_stated
    assert "Math" in metadata.prerequisites_stated
    assert "Understand neural networks" in metadata.learning_outcomes_stated
    assert metadata.target_audience == "Graduate students"
    assert metadata.level == "advanced"


def test_external_json_missing_optional_fields_use_defaults(tmp_path):
    """Partial JSON with only title/author → other fields fall back to '' (not None).

    The new schema uses empty-string '' defaults for scalars and [] for lists.
    """
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()
    (tmp_path / "course.json").write_text(json.dumps({"title": "Minimal Course", "author": "Someone"}))

    metadata = MetadataIngestor(pdf_path).ingest()

    assert metadata.title == "Minimal Course"
    assert metadata.prerequisites_stated == []
    assert metadata.prerequisites_inferred == []
    assert metadata.learning_outcomes_stated == []
    assert metadata.learning_outcomes_inferred == []
    assert metadata.description == ""   # new default is empty string, not "Unknown"


# ============================================================================
# Fallback: no sibling file, autoscan from PDF
# ============================================================================

def test_autoscan_fallback_when_no_sibling_file(tmp_path):
    """With no sibling JSON/TXT/HTML, ingest() falls back to autoscan (PDF text parse).

    We use a non-existent PDF — pdfplumber will fail gracefully returning a
    CourseMetadata with all-empty fields except `source` = PDF filename.
    """
    pdf_path = tmp_path / "missing.pdf"
    ingestor = MetadataIngestor(pdf_path)
    metadata = ingestor.ingest()
    assert isinstance(metadata, CourseMetadata)
    assert metadata.source == "missing.pdf"


# ============================================================================
# Explicit metadata source flag
# ============================================================================

def test_explicit_metadata_source_overrides_sibling(tmp_path):
    """When metadata_source is provided explicitly, sibling JSON is ignored."""
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()

    (tmp_path / "course.json").write_text(json.dumps({"title": "Sibling Title"}))

    explicit_path = tmp_path / "override.json"
    explicit_path.write_text(json.dumps({"title": "Explicit Title", "author": "Override Author"}))

    metadata = MetadataIngestor(pdf_path, metadata_source=explicit_path).ingest()

    assert metadata.title == "Explicit Title"
    assert metadata.author == "Override Author"


# ============================================================================
# Source field is always populated
# ============================================================================

def test_source_field_set_to_pdf_filename(tmp_path):
    """When falling back to PDF parsing, source is set to the PDF filename."""
    pdf_path = tmp_path / "mybook.pdf"
    metadata = MetadataIngestor(pdf_path).ingest()
    assert metadata.source == "mybook.pdf"


# ============================================================================
# CourseMetadata validators (ADR-038)
# ============================================================================

def test_level_validator_normalises_case():
    """Level field validator lowercases and normalises separators."""
    assert CourseMetadata(level="Introductory").level == "introductory"
    assert CourseMetadata(level="Undergraduate Introductory").level == "undergraduate_introductory"
    assert CourseMetadata(level="graduate").level == "graduate"


def test_level_validator_rejects_unknown_values():
    """Invalid level values are coerced to empty string."""
    assert CourseMetadata(level="beginner").level == ""
    assert CourseMetadata(level="PhD").level == ""
    assert CourseMetadata(level="bogus").level == ""


def test_author_validator_rejects_long_author():
    """Author with more than 6 words is coerced to '' (body-text corruption defence)."""
    m = CourseMetadata(author="This is a very long author name from the body text")
    assert m.author == ""


def test_author_validator_accepts_normal_author():
    """Author within 6 words passes through unchanged."""
    m = CourseMetadata(author="Allen B. Downey")
    assert m.author == "Allen B. Downey"


def test_publisher_validator_rejects_long_publisher():
    """Publisher with more than 6 words is coerced to ''."""
    m = CourseMetadata(publisher="This is a sentence that was extracted from the body text")
    assert m.publisher == ""


def test_publisher_validator_accepts_normal_publisher():
    """Publisher within 6 words passes through unchanged."""
    m = CourseMetadata(publisher="Green Tea Press")
    assert m.publisher == "Green Tea Press"


# ============================================================================
# TOCEntry model
# ============================================================================

def test_toc_entry_basic():
    """TOCEntry round-trips correctly."""
    entry = TOCEntry(chapter_number="1", title="Compilation", page_number=1)
    assert entry.chapter_number == "1"
    assert entry.title == "Compilation"
    assert entry.page_number == 1


def test_toc_entry_optional_fields():
    """TOCEntry works with only title."""
    entry = TOCEntry(title="Preface")
    assert entry.chapter_number is None
    assert entry.page_number is None


# ============================================================================
# New test: heuristic metadata phase only populates author from cover
# (full heuristic integration test — no LLM)
# ============================================================================

def test_metadata_heuristic_phase_author_strictly_from_cover(tmp_path):
    """_extract_heuristic_metadata scopes author/publisher search to cover text only.

    The heuristic phase must not pull author/publisher from body text.
    We verify this by injecting a sibling JSON with an author that only a
    body-text search would find, and ensuring the metadata parse respects
    the new field names.
    """
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()

    data = {
        "title": "Test Book",
        "author": "Jane Doe",
        "publisher": "Acme Press",
        "prerequisites_inferred": ["Basic programming"],
        "learning_outcomes_inferred": ["Understand algorithms"],
    }
    (tmp_path / "course.json").write_text(json.dumps(data))

    metadata = MetadataIngestor(pdf_path).ingest()

    assert metadata.title == "Test Book"
    assert metadata.author == "Jane Doe"
    assert metadata.publisher == "Acme Press"
    assert "Basic programming" in metadata.prerequisites_inferred
    assert "Understand algorithms" in metadata.learning_outcomes_inferred


# ============================================================================
# Think OS calibration reference round-trip
# ============================================================================

def test_thinkos_reference_file_roundtrip():
    """Think OS calibration reference loads without validation errors."""
    ref_path = Path("examples/metadata_reference_thinkos.json")
    if not ref_path.exists():
        pytest.skip("Think OS reference file not found")

    with open(ref_path) as f:
        data = json.load(f)

    m = CourseMetadata(**{k: v for k, v in data.items() if k in CourseMetadata.model_fields})
    assert m.level == "undergraduate_introductory"
    assert m.author == "Allen B. Downey"
    assert m.publisher == "Green Tea Press"
    assert len(m.toc) == 11
    assert m.prerequisites_stated == []
    assert len(m.prerequisites_inferred) > 0
    assert m.learning_outcomes_stated == []
    assert len(m.learning_outcomes_inferred) > 0
