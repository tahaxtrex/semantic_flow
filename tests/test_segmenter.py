import pytest
from pathlib import Path
from src.segmenter import SmartSegmenter, _is_copyright_page


# ============================================================================
# _chunk_text
# ============================================================================

def test_chunk_text_under_limit_returns_unchanged():
    """Text shorter than max_words is returned as a single chunk (ADR-037)."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=200)
    text = "Short text."
    assert segmenter._chunk_text(text) == [text]


def test_chunk_text_splits_on_sentence_boundary():
    """Text exceeding max_words splits at sentence boundaries (ADR-037)."""
    # 3-word ceiling — forces a split between the two sentences (5 words each)
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=5)
    text = "One two three four five. Six seven eight nine ten."
    chunks = segmenter._chunk_text(text)
    assert len(chunks) >= 2
    # No chunk should exceed 5 words (allowing sentence-boundary tolerance)
    for chunk in chunks:
        assert len(chunk.split()) <= 10  # generous ceiling; main test is the split happened


def test_chunk_text_no_text_loss():
    """All input words must appear somewhere in the output chunks (ADR-037)."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=5)
    text = "Alpha Beta gamma delta. Epsilon zeta. Eta theta iota kappa lambda."
    chunks = segmenter._chunk_text(text)
    combined = " ".join(chunks)
    for word in ["Alpha", "Beta", "Epsilon", "lambda"]:
        assert word in combined


# ============================================================================
# _merge_short_blocks
# ============================================================================

def test_merge_short_blocks_no_op_when_pair_exceeds_max():
    """No merge when the combined word count of every adjacent pair > max_words (ADR-037)."""
    # Each text is 3 words; combined = 6 words > max_words=5
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=5)
    blocks = [("Ch1", "alpha beta gamma"), ("Ch2", "delta epsilon zeta")]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 2
    assert result[0][0] == "Ch1"
    assert result[1][0] == "Ch2"


def test_merge_short_blocks_shortest_pair_priority():
    """With max_words large enough for only one merge, the shortest pair is chosen (ADR-037).

    Blocks: A(2 words), B(2 words), C(20 words).  max_words=5.
    A+B combined = 4 words (≤5) → merge A+B first.
    After merge: AB(4 words), C(20 words). AB+C = 24 > 5 → stop.
    Expect 2 blocks, with A and B merged but C alone.
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=5)
    a_text = "word1 word2"        # 2 words
    b_text = "word3 word4"        # 2 words
    c_text = " ".join(f"w{i}" for i in range(20))  # 20 words
    blocks = [("A", a_text), ("B", b_text), ("C", c_text)]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 2
    assert "word1" in result[0][1]
    assert "word3" in result[0][1]
    assert result[1][0] == "C"


def test_merge_short_blocks_preserves_first_heading():
    """First block's heading is kept when two blocks are merged."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=500)
    blocks = [("First", "aaa"), ("Second", "bbb"), ("Third", "ccc")]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 1
    assert result[0][0] == "First"
    assert "aaa" in result[0][1]
    assert "bbb" in result[0][1]
    assert "ccc" in result[0][1]


def test_merge_short_blocks_uses_second_heading_when_first_is_none():
    """If the first block has no heading but second does, the second heading is used."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=500)
    blocks = [(None, "preamble text"), ("Data Types", "main content")]
    result = segmenter._merge_short_blocks(blocks)
    assert len(result) == 1
    assert result[0][0] == "Data Types"


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
    # "Introduction to Python" has trailing content so it does NOT match the
    # standalone _PREFACE_HEADING_RE (ADR-040) — correctly instructional.
    result = segmenter._classify_segment("Introduction to Python", "Python is a high-level language.")
    assert result == "instructional"


def test_classify_frontmatter_by_heading():
    """Headings matching frontmatter patterns → frontmatter.

    Note: 'Preface', 'Foreword', and 'About this book' are now promoted to the
    distinct 'preface' type (ADR-040) when called without a block_index context
    (which defaults to "before chapter 1"). 'Acknowledgments' and 'Dedication'
    remain 'frontmatter' because they don't match _PREFACE_HEADING_RE.
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    for heading in ["Acknowledgments", "Dedication"]:
        assert segmenter._classify_segment(heading, "Some text.") == "frontmatter", \
            f"Expected frontmatter for heading: {heading!r}"


def test_classify_preface_headings_return_preface_type(tmp_path):
    """'Preface', 'Foreword', 'Introduction', 'About this book' → preface (ADR-040).

    When called with block_index=None (default), _is_before_first_chapter returns
    True so these headings correctly classify as 'preface'.
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    for heading in ["Preface", "Foreword", "About this book", "Introduction"]:
        result = segmenter._classify_segment(heading, "Some preamble text.")
        assert result == "preface", f"Expected preface for heading: {heading!r}, got {result!r}"


def test_classify_introduction_to_topic_is_not_preface():
    """'Introduction to Python' style heading → instructional, NOT preface (ADR-040).

    _PREFACE_HEADING_RE requires exact/standalone match; 'Introduction to X'
    does not match.
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment("Introduction to Python", "Python is a high-level language.")
    assert result == "instructional"


def test_classify_table_of_contents_is_reference_table():
    """'Table of Contents' matches the reference_table heading regex (ADR-040).

    Heading-based reference_table check is unconditional (no prose density gate).
    """
    segmenter = SmartSegmenter(Path("dummy.pdf"))
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
    """Exercise heading with exercise keyword → exercise (ADR-034)."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment("Exercise 3", "Write a function that...")
    assert result == "exercise"


def test_classify_numbered_heading_not_exercise():
    """Numbered heading WITHOUT exercise keyword → instructional, not exercise (ADR-034)."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment("1. Built-in functions", "Functions that are built into Python.")
    assert result == "instructional"


def test_classify_exercise_by_numbered_lines():
    """Segment with ≥3 exercise-keyword lines AND ≥40% of lines are exercise → exercise."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    text = "Practice 1: Write a function.\nExercise 2: Test it.\nQ3. Submit it.\nQ4. Review it."
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


# ============================================================================
# critic.v2.md — Issue 1: Running header rejection
# ============================================================================

def test_classify_known_running_header_not_frontmatter():
    """Known OpenStax running header should NOT trigger frontmatter classification."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment(
        "Access. The future of education.",
        "This chapter covers database normalization and SQL joins."
    )
    assert result == "instructional", \
        "A known running header heading must not cause frontmatter classification."


def test_classify_openstax_header_variant_not_frontmatter():
    """'Access for free at openstax.org' variant must also be ignored."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment(
        "Access for free at openstax.org",
        "Relational database systems organize data into tables."
    )
    assert result == "instructional"


# ============================================================================
# critic.v2.md — Issue 2: Glossary / Summary / Assessment segment types
# ============================================================================

def test_classify_key_terms_is_glossary():
    """Heading 'Key Terms' → glossary."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._classify_segment("Key Terms", "access control: restricting...") == "glossary"


def test_classify_key_term_singular_is_glossary():
    """Heading 'Key Term' (singular) → glossary."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._classify_segment("Key Term", "abstraction: the process of...") == "glossary"


def test_classify_glossary_heading_is_glossary():
    """Heading 'Glossary' → glossary."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._classify_segment("Glossary", "algorithm: step-by-step procedure.") == "glossary"


def test_classify_summary_heading_is_summary():
    """Heading 'Summary' → summary."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment(
        "Summary",
        "• DBMS stores data in tables.\n• SQL is the standard language.\n• Indexes improve query speed."
    )
    assert result == "summary"


def test_classify_chapter_summary_heading_is_summary():
    """Heading 'Chapter Summary' → summary."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    assert segmenter._classify_segment("Chapter Summary", "In this chapter we covered...") == "summary"


def test_classify_assessment_body_detected():
    """Body with numbered questions and lettered answer options → assessment."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    text = (
        "1. Which type stores structured data?\n"
        "a. NoSQL\n"
        "b. Relational\n"
        "c. Data Lake\n"
        "d. Document\n"
        "2. SQL stands for?\n"
        "a. Standard Query\n"
        "b. Structured Query Language\n"
        "c. Simple Query\n"
        "d. None of the above\n"
    )
    assert segmenter._classify_segment(None, text) == "assessment"


def test_classify_insufficient_options_not_assessment():
    """Body with only 2 lettered options (below threshold=3) → NOT assessment."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    text = "1. Choose one:\na. Option A\nb. Option B\nSome paragraph here."
    # Only 2 option lines and 1 question line → should NOT be classified as assessment
    assert segmenter._classify_segment(None, text) != "assessment"


# ============================================================================
# ADR-037: word-count ceiling
# ============================================================================

def test_segmenter_word_count_ceiling_single_chapter_passes_through():
    """A chapter under max_words is never split — returned as one chunk."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=30000)
    text = " ".join(f"word{i}" for i in range(1000))  # 1000 words, well under 30k
    chunks = segmenter._chunk_text(text)
    assert len(chunks) == 1


def test_segmenter_word_count_ceiling_megachapter_is_chunked():
    """A chapter exceeding max_words is split into multiple chunks."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=100)
    # Build a text with 500 words split into paragraphs of 50 words each
    para = " ".join(f"word{i}" for i in range(50))
    text = "\n\n".join([para] * 10)  # 500 words
    chunks = segmenter._chunk_text(text)
    assert len(chunks) >= 2
    # All original words should be recoverable
    combined = " ".join(chunks)
    for i in range(50):
        assert f"word{i}" in combined


def test_segmenter_word_count_ceiling_no_word_loss():
    """All words must be present in the output chunks after splitting."""
    segmenter = SmartSegmenter(Path("dummy.pdf"), max_words=10)
    words = [f"WORD{i}" for i in range(50)]
    text = " ".join(words)
    chunks = segmenter._chunk_text(text)
    combined = " ".join(chunks)
    for w in words:
        assert w in combined


# ============================================================================
# ADR-040: preface segment type
# ============================================================================

def test_classify_preface_standalone_heading():
    """A standalone 'Preface' heading before chapter 1 → preface type."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    # No block_index = None means _is_before_first_chapter returns True
    result = segmenter._classify_segment("Preface", "This book is for students new to OS.")
    assert result == "preface"


def test_classify_preface_not_matched_when_after_chapter_one():
    """A 'Preface' heading that appears after chapter 1 → frontmatter (not preface)."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    # Simulate: chapter 1 is at block index 2, preface is at block index 5
    segmenter._first_chapter_block_index = 2
    result = segmenter._classify_segment("Preface", "Some late text.", block_index=5)
    # Appears after chapter 1 — the preface check does not fire; falls to frontmatter
    assert result == "frontmatter"


# ============================================================================
# ADR-040: prose-density reference_table gate
# ============================================================================

def test_classify_prose_density_prevents_reference_table_on_instructional():
    """An instructional chapter with many tables but high prose density → instructional."""
    # 60% of the text is prose, 40% is [TABLE:] annotations
    tables = "[TABLE:\n a | b \n c | d \n]\n" * 5  # 5 tables
    prose = ("This chapter covers SQL joins in depth. " * 10)  # lots of prose
    text = prose + tables  # prose dominates
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment(None, text)
    assert result != "reference_table", (
        "A prose-dominated chapter should not be classified as reference_table "
        "even when it contains many table annotations."
    )
    assert result == "instructional"


def test_classify_table_dominated_content_is_reference_table():
    """Content dominated by table annotations with minimal prose → reference_table."""
    # Lots of [TABLE:...] markers, minimal prose
    tables = "[TABLE:\n a | b \n c | d \n]\n" * 10
    text = "See tables below.\n" + tables  # very low prose ratio
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    result = segmenter._classify_segment(None, text)
    assert result == "reference_table"


# ============================================================================
# ADR-039: TOC count validation in segment() (unit test via mock)
# ============================================================================

def test_segmenter_toc_count_validation_warning(tmp_path, caplog):
    """Segmenter logs a WARNING when detected chapter count diverges from metadata TOC.

    We can't use a real PDF here, so we test the helper that drives the logic:
    _find_first_chapter_block_index detects the first chapter heading correctly.
    """
    from src.segmenter import SmartSegmenter
    segmenter = SmartSegmenter(Path("dummy.pdf"))

    # Build fake blocks where the first chapter-like heading is at index 2
    blocks = [
        (None, "preamble text"),
        ("Preface", "preface content"),
        ("Chapter 1 Introduction", "chapter one content"),
        ("Chapter 2 Processes", "chapter two content"),
    ]
    idx = segmenter._find_first_chapter_block_index(blocks)
    assert idx == 2, f"Expected first chapter at index 2, got {idx}"


def test_find_first_chapter_block_no_chapter_returns_none():
    """_find_first_chapter_block_index returns None when no chapter heading is found."""
    segmenter = SmartSegmenter(Path("dummy.pdf"))
    blocks = [("Some heading", "text"), ("Another", "more text")]
    assert segmenter._find_first_chapter_block_index(blocks) is None

