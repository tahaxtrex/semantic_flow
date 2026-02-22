import json
from pathlib import Path
from src.metadata import MetadataIngestor

def test_external_json_extraction(tmp_path):
    # Setup mock files
    pdf_path = tmp_path / "course.pdf"
    pdf_path.touch()
    
    json_path = tmp_path / "course.json"
    data = {
        "title": "Advanced AI",
        "author": "Dr. Smith",
        "prerequisites": ["Python", "Math"]
    }
    json_path.write_text(json.dumps(data))
    
    ingestor = MetadataIngestor(pdf_path)
    metadata = ingestor.ingest()
    
    assert metadata.title == "Advanced AI"
    assert metadata.author == "Dr. Smith"
    assert "Python" in metadata.prerequisites
    assert "Math" in metadata.prerequisites
