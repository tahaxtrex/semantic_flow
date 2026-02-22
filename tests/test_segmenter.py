from pathlib import Path
from src.segmenter import SmartSegmenter

def test_safe_chunking_fallback():
    # We can test the private chunking method directly
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=40)
    
    text = "This is a short sentence. This is another sentence that pushes it."
    chunks = segmenter._chunk_text(text)
    
    # Needs to break into at least two arrays preventing truncation
    assert len(chunks) == 2
    assert "This is a short sentence." in chunks[0]
