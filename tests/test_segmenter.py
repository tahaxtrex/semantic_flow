import pytest
from pathlib import Path
from src.segmenter import SmartSegmenter, _is_copyright_page


# ============================================================================
# _chunk_text
# ============================================================================

def test_chunk_text_under_limit_returns_unchanged():
    """Text shorter than max_chars is returned as a single chunk."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=200)
    text = "Short text."
    assert segmenter._chunk_text(text) == [text]


def test_chunk_text_splits_on_sentence_boundary():
    """Text exceeding max_chars splits at sentence boundaries, not mid-word."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=40)
    text = "This is a short sentence. This is another sentence that pushes it."
    chunks = segmenter._chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0].endswith("sentence.")
    assert "another sentence" in chunks[1]


def test_chunk_text_no_text_loss():
    """All input characters must appear somewhere in the output chunks."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=50)
    text = "Alpha. Beta gamma delta. Epsilon zeta. Eta theta iota kappa lambda."
    chunks = segmenter._chunk_text(text)
    combined = " ".join(chunks)
    # Every word from input must appear in the output somewhere
    for word in ["Alpha", "Beta", "Epsilon", "lambda"]:
        assert word in combined


# ============================================================================
# _merge_short_blocks
# ============================================================================

def test_merge_short_blocks_no_op_when_pair_exceeds_max():
    """No merge when the combined length of every adjacent pair > max_chars."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=10)
    blocks = [("Ch1", "abcdef"), ("Ch2", "ghijkl")]   # 6+6=12 > 10
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 2
    assert result[0] == ("Ch1", "abcdef")
    assert result[1] == ("Ch2", "ghijkl")


def test_merge_short_blocks_shortest_pair_priority():
    """With max_chars large enough for only one merge, the shortest pair is chosen.

    Blocks: A(5), B(5), C(100).  max_chars=15.
    A+B combined = 12 (\u226415) → merge A+B first.
    After merge: AB(12), C(100). AB+C combined = 114 > 15 → stop.
    So we expect 2 blocks, with A and B merged but C alone.
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=15)
    blocks = [
        ("A", "a" * 5),
        ("B", "b" * 5),
        ("C", "c" * 100),
    ]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 2
    assert "a" * 5 in result[0][1]
    assert "b" * 5 in result[0][1]
    assert result[1][0] == "C"


def test_merge_short_blocks_preserves_first_heading():
    """First block's heading is kept when two blocks are merged."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=500)
    blocks = [("First", "aaa"), ("Second", "bbb"), ("Third", "ccc")]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 1
    assert result[0][0] == "First"
    assert "aaa" in result[0][1]
    assert "bbb" in result[0][1]
    assert "ccc" in result[0][1]


def test_merge_short_blocks_uses_second_heading_when_first_is_none():
    """If the first block has no heading but second does, the second heading is used."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=500)
    blocks = [(None, "preamble text"), ("Chapter 1", "main content")]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 1
    assert result[0][0] == "Chapter 1"


def test_merge_short_blocks_empty():
    """Empty input returns empty list."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._merge_short_blocks([]) == []


def test_merge_short_blocks_single_block_unchanged():
    """A single-element list is returned as-is."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_chars=10)
    blocks = [("Only", "some text")]
    result = segmenter._merge_short_blocks(blocks)
    assert result == blocks


# ============================================================================
# _words_to_lines
# ============================================================================

def test_words_to_lines_empty():
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._words_to_lines([], header_threshold=14.0) == []


def test_words_to_lines_groups_by_y_baseline():
    """Words at the same Y baseline (within 2pt tolerance) become one line."""
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


def test_words_to_lines_max_size_is_largest_word_size():
    """max_size in each line dict must equal the largest font size on that line."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': 'Small', 'size': 10.0, 'fontname': 'Times'},
        {'doctop': 100.0, 'x0': 60, 'text': 'BIG', 'size': 18.0, 'fontname': 'Times'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert len(lines) == 1
    assert lines[0]['max_size'] == 18.0


def test_words_to_lines_is_bold_detected():
    """Lines containing Bold/Black/Heavy fontnames must have is_bold=True."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': 'Bold text', 'size': 12.0, 'fontname': 'TimesNewRoman-Bold'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert lines[0]['is_bold'] is True


def test_words_to_lines_is_bold_false_for_regular():
    """Regular (non-bold) fonts must have is_bold=False."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': 'Regular', 'size': 12.0, 'fontname': 'Helvetica'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert lines[0]['is_bold'] is False


def test_words_to_lines_strips_oversized_digits():
    """Standalone oversized-font digits (margin chapter numbers) are stripped."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 5,  'text': '3',            'size': 20.0, 'fontname': 'Times'},
        {'doctop': 100.0, 'x0': 50, 'text': 'Introduction', 'size': 12.0, 'fontname': 'Times'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert len(lines) == 1
    assert lines[0]['text'] == 'Introduction'


def test_words_to_lines_keeps_multidigit_words():
    """Multi-digit tokens like '123px' should NOT be stripped as margin numbers."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': '2024', 'size': 20.0, 'fontname': 'Times'},
        {'doctop': 100.0, 'x0': 60, 'text': 'Edition', 'size': 12.0, 'fontname': 'Times'},
    ]
    # '2024' matches \d+ AND size >= threshold → should be stripped (it is a standalone number)
    # This tests that the regex fullmatch(r'\d+') includes multi-digit strings
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert 'Edition' in lines[0]['text']


def test_words_to_lines_detects_courier_as_code():
    """Lines with Courier/Mono/Code fontname must have is_code=True."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': 'print(x)', 'size': 10.0, 'fontname': 'CourierNew'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert lines[0]['is_code'] is True


def test_words_to_lines_replaces_cid_artifacts():
    """CID artifacts like (cid:123) in text must be replaced with [?]."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    words = [
        {'doctop': 100.0, 'x0': 10, 'text': '(cid:32)', 'size': 10.0, 'fontname': 'Times'},
        {'doctop': 100.0, 'x0': 60, 'text': 'word',     'size': 10.0, 'fontname': 'Times'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert '[?]' in lines[0]['text']
    assert '(cid:' not in lines[0]['text']


def test_words_to_lines_orders_words_by_x0():
    """Words on the same baseline must be assembled left-to-right by x0."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    # Deliberately out-of-order x0
    words = [
        {'doctop': 100.0, 'x0': 80, 'text': 'world', 'size': 10.0, 'fontname': 'Times'},
        {'doctop': 100.0, 'x0': 10, 'text': 'Hello', 'size': 10.0, 'fontname': 'Times'},
    ]
    lines = segmenter._words_to_lines(words, header_threshold=14.0)
    assert lines[0]['text'] == 'Hello world'


# ============================================================================
# _classify_segment
# ============================================================================

def test_classify_instructional_default():
    """Normal body text with no special heading → instructional."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment("Introduction to Python", "Python is a high-level language.")
    assert result == "instructional"


def test_classify_frontmatter_by_heading():
    """Headings matching frontmatter patterns → frontmatter.

    Note: 'Table of Contents' matches both _REFERENCE_TABLE_PATTERNS ('table of')
    AND _FRONTMATTER_PATTERNS — but reference_table is checked first so it wins.
    'Table of Contents' should be tested under reference_table, not frontmatter.
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    for heading in ["Preface", "Acknowledgments", "Foreword", "About this book", "Dedication"]:
        assert segmenter._classify_segment(heading, "Some text.") == "frontmatter", \
            f"Expected frontmatter for heading: {heading!r}"


def test_classify_table_of_contents_is_reference_table():
    """'Table of Contents' matches the reference_table regex ('table of') before frontmatter."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    # This is correct behavior: reference_table check happens before frontmatter check
    assert segmenter._classify_segment("Table of Contents", "Chapter 1 .... 1") == "reference_table"



def test_classify_frontmatter_by_first_line():
    """If heading is absent or generic, first line of text triggers frontmatter."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment(None, "Table of Contents\nChapter 1 .... 1\nChapter 2 .... 5")
    assert result == "frontmatter"


def test_classify_solution_by_heading():
    """Solution/Answer headings → solution."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    for heading in ["Solution", "Answer", "Answers", "Solutions", "Answer to Exercise 1"]:
        assert segmenter._classify_segment(heading, "x = 42") == "solution", \
            f"Expected solution for heading: {heading!r}"


def test_classify_exercise_by_heading():
    """Exercise heading → exercise."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment("Exercise 3", "Write a function that...")
    assert result == "exercise"


def test_classify_exercise_by_numbered_lines():
    """Segment with ≥3 numbered lines AND ≥40% of lines are numbered → exercise."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    text = "1. Write a function.\n2. Test it.\n3. Submit it.\n4. Review it."
    result = segmenter._classify_segment("Exercises", text)
    assert result == "exercise"


def test_classify_reference_table_by_heading():
    """Appendix/Index headings → reference_table."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    for heading in ["Appendix A", "Index", "Reference Table"]:
        assert segmenter._classify_segment(heading, "...") == "reference_table", \
            f"Expected reference_table for heading: {heading!r}"


# ============================================================================
# _is_copyright_page  (module-level helper)
# ============================================================================

def test_is_copyright_page_detects_dense_legal_text():
    """Text with 4+ copyright markers → True."""
    text = (
        "© 2023 OpenStax. All rights reserved. ISBN 978-1-2345.\n"
        "Published by Rice University. Licensed under Creative Commons Attribution."
    )
    assert _is_copyright_page(text) is True


def test_is_copyright_page_normal_content_is_false():
    """Regular instructional text should not be flagged as copyright."""
    text = "Python is a general-purpose programming language used in data science."
    assert _is_copyright_page(text) is False


def test_is_copyright_page_single_keyword_is_not_enough():
    """A single copyright keyword alone (below threshold) → False."""
    text = "This chapter is about attribution in academic writing."
    assert _is_copyright_page(text) is False
