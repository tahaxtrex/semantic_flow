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


def test_merge_short_blocks_all_short():
    """All blocks shorter than min_chars should be merged into a single segment."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), min_chars=100)
    blocks = [
        ("Chapter 1", "Short text."),
        ("Chapter 2", "Also short."),
        ("Chapter 3", "Still short."),
    ]
    merged = segmenter._merge_short_blocks(blocks)
    # All merged into one because they're all below min_chars individually
    assert len(merged) == 1
    assert "Short text." in merged[0][1]
    assert "Also short." in merged[0][1]
    assert "Still short." in merged[0][1]
    # Heading from the first block should be preserved
    assert merged[0][0] == "Chapter 1"


def test_merge_short_blocks_preserves_large():
    """Blocks at or above min_chars should not be merged with the next block."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), min_chars=10)
    blocks = [
        ("Intro", "This is long enough content."),     # 28 chars >= 10
        ("Body", "More content here."),                # 18 chars >= 10
    ]
    merged = segmenter._merge_short_blocks(blocks)
    assert len(merged) == 2


def test_merge_short_blocks_trailing_appended():
    """Trailing short content should be appended to the last emitted block."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), min_chars=30)
    blocks = [
        ("Ch1", "This block is long enough to emit."),  # 34 chars >= 30
        ("Ch2", "Tiny."),                               # 5 chars < 30
    ]
    merged = segmenter._merge_short_blocks(blocks)
    assert len(merged) == 1
    assert "Tiny." in merged[0][1]


def test_merge_short_blocks_empty():
    """Empty input returns empty list."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._merge_short_blocks([]) == []
