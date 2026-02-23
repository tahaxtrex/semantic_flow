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


def test_merge_to_target_already_at_limit():
    """When blocks == max_segments, no merging should occur."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    blocks = [("Ch1", "text one"), ("Ch2", "text two")]
    result = segmenter._merge_to_target(blocks, max_segments=2)
    assert len(result) == 2
    assert result[0] == ("Ch1", "text one")
    assert result[1] == ("Ch2", "text two")


def test_merge_to_target_merges_shortest_pair():
    """Merges the pair with the smallest combined text length first."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    blocks = [
        ("Ch1", "Short."),           # 6 chars
        ("Ch2", "Also short."),      # 11 chars
        ("Ch3", "A much longer block of text here."),  # 33 chars
    ]
    # max_segments=2 means one merge: Ch1+Ch2 is shortest pair (17), Ch2+Ch3 is 44
    result = segmenter._merge_to_target(blocks, max_segments=2)
    assert len(result) == 2
    assert result[0][0] == "Ch1"
    assert "Short." in result[0][1]
    assert "Also short." in result[0][1]
    assert result[1][0] == "Ch3"


def test_merge_to_target_preserves_first_heading():
    """Heading from the first block in a merged pair is kept."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    blocks = [
        ("First", "aaa"),
        ("Second", "bbb"),
        ("Third", "ccc"),
    ]
    result = segmenter._merge_to_target(blocks, max_segments=1)
    assert len(result) == 1
    assert result[0][0] == "First"
    assert "aaa" in result[0][1]
    assert "bbb" in result[0][1]
    assert "ccc" in result[0][1]


def test_merge_to_target_empty():
    """Empty input returns empty list."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._merge_to_target([], max_segments=4) == []


def test_words_to_lines_empty():
    """Empty word list returns empty result."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._words_to_lines([], header_threshold=14.0) == []


def test_words_to_lines_groups_by_y():
    """Words at the same Y baseline are joined into one line."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': 'Hello', 'size': 10.0, 'fontname': 'Times'},
        {'doctop': 100.0, 'x0': 50, 'text': 'world', 'size': 10.0, 'fontname': 'Times'},
        {'doctop': 120.0, 'x0': 10, 'text': 'Goodbye', 'size': 10.0, 'fontname': 'Times'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert len(lines) == 2
    assert lines[0]['text'] == 'Hello world'
    assert lines[1]['text'] == 'Goodbye'


def test_words_to_lines_strips_oversized_digits():
    """Standalone oversized-font digits (margin chapter numbers) are stripped."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 5, 'text': '3', 'size': 20.0, 'fontname': 'Times'},   # oversized digit — strip
        {'doctop': 100.0, 'x0': 50, 'text': 'Introduction', 'size': 12.0, 'fontname': 'Times'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert len(lines) == 1
    assert lines[0]['text'] == 'Introduction'


def test_words_to_lines_detects_code_font():
    """Lines using Courier or Mono fontnames are flagged is_code=True."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': 'print(x)', 'size': 10.0, 'fontname': 'CourierNew'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert len(lines) == 1
    assert lines[0]['is_code'] is True
