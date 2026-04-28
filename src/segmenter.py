import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional, TYPE_CHECKING
import pdfplumber
import re

from src.models import Segment

if TYPE_CHECKING:
    from src.metadata import CourseMetadata

logger = logging.getLogger(__name__)

# ── Word counting (ADR-037) ───────────────────────────────────────────────────
_WORD_RE = re.compile(r"\S+")


def _word_count(text: str) -> int:
    """Count whitespace-delimited tokens. Used as the proxy for token budget
    under the soft `max_words` ceiling (ADR-037)."""
    if not text:
        return 0
    return len(_WORD_RE.findall(text))

# OCR availability flag — set on first import attempt
_OCR_AVAILABLE: Optional[bool] = None

def _check_ocr_available() -> bool:
    """Check once whether tesseract + pdf2image are available."""
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is None:
        try:
            import pytesseract
            import pdf2image
            pytesseract.get_tesseract_version()  # raises if not installed
            _OCR_AVAILABLE = True
        except Exception:
            _OCR_AVAILABLE = False
    return _OCR_AVAILABLE

def _ocr_page(pdf_path: Path, page_index: int) -> str:
    """Render a PDF page to an image and extract text via Tesseract OCR.

    Args:
        pdf_path: Path to the PDF file.
        page_index: 0-based page index.

    Returns:
        Extracted plain text, or empty string on failure.
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
        images = convert_from_path(
            str(pdf_path),
            dpi=300,
            first_page=page_index + 1,
            last_page=page_index + 1,
        )
        if not images:
            return ""
        text = pytesseract.image_to_string(images[0], lang="eng")
        return text.strip()
    except Exception as e:
        logger.warning(f"OCR failed for page {page_index + 1}: {e}")
        return ""

# Regex to replace CID artifact placeholders (critic.md Issue 9)
_CID_RE = re.compile(r'\(cid:\d+\)')

# Regex to detect Figure references (critic.md Issue 6)
_FIG_REF_RE = re.compile(r'Fig\.?\s*(\d+\.\d+)\b', re.IGNORECASE)

# Segment type detection patterns (critic.md Issue 8)
# ADR-034 (critic.v3 Issue 3): Exercise heading pattern now requires exercise-specific
# keywords AFTER the number. '1. Built-in functions' no longer triggers.
# Extended: non-numbered heading variants like "Review Questions", "Checkpoint", etc.
_EXERCISE_HEADING_PATTERN = re.compile(
    r'^\d+[.)\s]\s*(exercise|practice|problem|question|write\s+a\s+program|assignment|task\s+\d)'
    r'|^(review\s+questions?|practice\s+problems?|checkpoint|try\s+it(\s+yourself)?'
    r'|self[\-\s]?check|knowledge\s+check|lab\s+exercise|hands[\-\s]on(\s+activity)?'
    r'|activity\s+\d|coding\s+(exercise|challenge)|programming\s+(exercise|challenge))',
    re.IGNORECASE,
)
_EXERCISE_PATTERNS = [
    re.compile(r'^(Practice\s+\d|Exercise\s+\d|Q\d+[\.\)])', re.IGNORECASE),
    re.compile(r'\b(write a program|create a|implement|design a)\b', re.IGNORECASE),
]
# Extended: "Worked Example", "Sample Output", "Expected Output" alongside existing patterns
_SOLUTION_PATTERNS = [
    re.compile(r'^(Solution|Answer|Answers|Solutions)\b', re.IGNORECASE),
    re.compile(r'^(Answer to|Solution to)\b', re.IGNORECASE),
    re.compile(r'^(Worked\s+Example|Sample\s+Output|Expected\s+Output)\b', re.IGNORECASE),
]
# Extended: quick-reference and API/command reference headings beyond appendix/index
_REFERENCE_TABLE_PATTERNS = [
    re.compile(
        r'\b(appendix|table of|reference table|index|quick\s+reference|cheat\s+sheet'
        r'|summary\s+table|method\s+summary|api\s+reference|command\s+reference)\b',
        re.IGNORECASE,
    ),
]
_FRONTMATTER_PATTERNS = [
    # ADR-040: `preface`, `foreword`, `introduction`, `about this book` are now
    # handled separately by `_PREFACE_HEADING_RE` so they can be classified as
    # `preface` (a distinct Course Gate context type) rather than bypassed
    # alongside copyright / dedication / TOC. They remain in this list as a
    # safety net for the case where `_is_before_chapter_one()` cannot be
    # determined.
    re.compile(r'^(\s*(table of contents|contents|preface|acknowledgments|about this book|history|sources|foreword|dedication|bibliography|glossary|index|appendix|abbreviations|list of figures|list of tables)\s*)$', re.IGNORECASE),
    re.compile(r'^(about\s+\w[\w\s]+|coverage\s+and\s+scope|pedagogical\s+foundation)', re.IGNORECASE),
    # ADR-034 (critic.v3 Issue 4): Institutional boilerplate
    re.compile(r'(UGC|AICTE|JNTUH|JNTU|affiliated\s+to|accredited\s+by|autonomous\s+institution)', re.IGNORECASE),
    re.compile(r'(syllabus|course\s+code|credit\s+hours|scheme\s+of\s+instruction)', re.IGNORECASE),
]

# ADR-040: Preface headings promoted to a distinct segment type. Matched BEFORE
# _FRONTMATTER_PATTERNS in `_classify_segment()`.
# Uses exact/tight matching (no trailing content after the keyword) so that
# chapter headings like "Introduction to Python" do NOT match.
_PREFACE_HEADING_RE = re.compile(
    r'^\s*(?:preface|foreword|about\s+this\s+(?:book|text)|introduction)\s*$',
    re.IGNORECASE,
)

# ADR-040: Prose-density check for reference_table classification.
_BULLET_LINE_RE = re.compile(r'^\s*(?:[-*•●◦▪‣·]|\d+[.)])\s')
_CODE_BLOCK_RE = re.compile(r'\[CODE\].*?\[/CODE\]', re.DOTALL)
_TABLE_ANNOTATION_RE = re.compile(r'\[TABLE:(.*?)\]', re.DOTALL)


def _compute_prose_density(text: str) -> float:
    """Return the ratio of prose characters (non-table, non-code, non-bullet)
    to total characters. Used by `_classify_segment()` (ADR-040) to gate the
    `reference_table` assignment — a prose-heavy chapter that merely *contains*
    tables is not a reference table.

    Returns 0.0 for empty input.
    """
    if not text:
        return 0.0
    total = len(text)

    # Detect miswrapped prose masquerading as table content
    prose_in_tables = 0
    # Ensure _TABLE_ANNOTATION_RE uses a capture group: r'\[TABLE:(.*?)\]'
    for match in _TABLE_ANNOTATION_RE.finditer(text):
        table_content = match.group(1)
        for line in table_content.splitlines():
            # Heuristic: A line inside a table with >10 words is likely prose.
            # (Note: This threshold should be empirically validated against a dataset of known tables)
            if len(line.split()) > 10:
                prose_in_tables += len(line)

    # strip table annotations (pdfplumber-injected markers)
    stripped = _TABLE_ANNOTATION_RE.sub('', text)
    # strip [CODE]...[/CODE] regions
    stripped = _CODE_BLOCK_RE.sub('', stripped)
    # strip bullet/numbered list lines
    prose_lines = [
        ln for ln in stripped.splitlines()
        if ln.strip() and not _BULLET_LINE_RE.match(ln)
    ]
    prose_chars = sum(len(ln) for ln in prose_lines) + prose_in_tables
    return prose_chars / total if total else 0.0
# ADR-029 (critic.v3 Issue 1): Bold labels that are too common to be headings
_BOLD_LABEL_EXCLUSIONS = frozenset({
    'example', 'syntax', 'note', 'output', 'input', 'definition',
    'program', 'result', 'solution', 'answer', 'explanation',
})
# ADR-029: Visual TOC line pattern (UNIT I ... 1, Chapter 3 ... 45, etc.)
_VISUAL_TOC_LINE_RE = re.compile(
    r'(UNIT|Chapter|Module|Part)\s+([\dIVXivx]+)[^\d]*?(\d{1,4})\s*$',
    re.IGNORECASE,
)

# critic.v2.md Issue 1 — known publisher/platform running headers that must never
# trigger frontmatter classification.  Compared lowercase-stripped.
_KNOWN_RUNNING_HEADERS: frozenset = frozenset({
    "access. the future of education.",
    "access for free at openstax.org",
    "access for free at",
    "openstax",
})

# critic.v2.md Issue 2 — end-of-chapter structural patterns that should NOT be
# scored as instructional content.
_GLOSSARY_HEADING_PATTERNS = [
    re.compile(r'^(key\s+terms?|glossary|definitions?|terminology|key\s+vocabulary)$', re.IGNORECASE),
]
_SUMMARY_HEADING_PATTERNS = [
    re.compile(r'^(summary|chapter\s+summary|module\s+summary|section\s+summary)$', re.IGNORECASE),
]
# Lines that are a lettered answer option: "a. ...", "b. ...", "c. ...", "d. ..."
_ASSESSMENT_OPTION_RE = re.compile(r'^[a-d]\.\s+\S', re.IGNORECASE)
# Lines that look like numbered questions: "1. ...", "2) ..."
_NUMBERED_QUESTION_RE = re.compile(r'^\d+[.)\s]\s*\S')

# Heuristics to detect copyright / license pages by content density of legal keywords
_COPYRIGHT_KEYWORDS = [
    'creative commons', 'all rights reserved', 'isbn', 'doi:', 'published by',
    'reproduced without', 'prior written consent',
    'licensed under', 'licensing, please contact', 'rice university',
    'openstax', 'kendall hunt', 'arnold ventures', 'chan zuckerberg',
    'attribution', 'noncommercial',
]

def _is_copyright_page(text: str) -> bool:
    """Return True if the text is predominantly copyright/legal boilerplate."""
    lower = text.lower()
    # Also count © symbol occurrences (works for ©2025 and © 2025)
    symbol_hits = lower.count('©')
    keyword_hits = sum(1 for kw in _COPYRIGHT_KEYWORDS if kw in lower)
    total_hits = keyword_hits + (2 if symbol_hits >= 1 else 0)
    # If 4+ copyright markers found, it's frontmatter
    return total_hits >= 4


class SmartSegmenter:
    """Deterministically extracts and segments text from PDFs using font heuristics.

    Targets chapter-level granularity capped at 1 segment per 10 pages.
    Header detection requires a significant font-size jump (>= 1.4x body median)
    so only true chapter/section titles trigger a new segment.

    Text reconstruction uses word objects (not character streams) to correctly handle
    ligature-encoded fonts where character-level extraction concatenates words.
    Running headers/footers are stripped by cropping the body region (top 10%, bottom 8%).
    Tables are detected via pdfplumber and annotated as [TABLE: ...] markers.

    Critic fixes applied:
    - Issue 6: Figure captions extracted and annotated as [FIGURE X.Y: caption]
    - Issue 8: Segment type classified (instructional/exercise/solution/reference_table)
    - Issue 9: CID artifacts replaced with [?]
    """

    def __init__(
        self,
        pdf_path: Path,
        course_metadata: Optional["CourseMetadata"] = None,
        max_words: int = 30000,
        max_chars: Optional[int] = None,
        min_chars: int = 600,
        bold_as_header: bool = True,
    ):
        """Initialise the segmenter.

        Args:
            pdf_path: Path to the PDF file.
            course_metadata: Optional parsed CourseMetadata (ADR-039). When
                supplied, `course_metadata.toc` is used as a reference chapter
                count to validate Tier 1 (bookmark outline) and Tier 2 (UNIT
                marker scan) results.
            max_words: Soft word-count ceiling (ADR-037, Q-031). Default 30,000
                words (≈40,000 tokens). A single chapter is never split unless
                it exceeds this ceiling. Pathological megachapters are chunked
                at paragraph → sentence → word boundaries.
            max_chars: Deprecated. Accepted for backwards compat; coerced to a
                word-count via a 5-chars/word approximation with a warning.
            min_chars: Kept for backwards compat; unused in the word-count
                merge path.
            bold_as_header: ADR-029 — whether bold text qualifies as a header.
        """
        self.pdf_path = Path(pdf_path)
        self.course_metadata = course_metadata

        if max_chars is not None:
            logger.warning(
                "SmartSegmenter: max_chars is deprecated; use max_words. "
                "Coercing %d chars → ~%d words.",
                max_chars,
                max_chars // 5,
            )
            max_words = max(max_words, max_chars // 5)
        self.max_words = max_words
        self.min_chars = min_chars  # kept for backward compat
        self.bold_as_header = bold_as_header  # ADR-029: toggle bold heading detection
        # Populated during `segment()` so `_classify_segment()` can tell whether
        # a preface-titled segment appears before chapter 1.
        self._first_chapter_block_index: Optional[int] = None

    def segment(self) -> List[Segment]:
        """Extract text from PDF, group by major headers, and apply fallback chunking.

        Extraction strategy (ADR-023 + ADR-039 three-tier hierarchy with
        TOC-count validation):
          1. TOC path (bookmark outline): If ≥2 entries, use the PDF outline to
             derive chapter-precise page ranges. Validated against
             `course_metadata.toc` chapter count (±2 tolerance).
          2. UNIT marker scan: Falls through on count mismatch.
          3. Font-heuristic path: Final tier. Logs warning on count mismatch
             but does not fall through further.

        Blocks are then merged (ADR-037 word-count ceiling) and any megachapter
        exceeding `max_words` is chunked at paragraph → sentence → word
        boundaries.
        """
        logger.info(f"Segmenting PDF: {self.pdf_path.name}")

        # ADR-039: TOC-count validation reference
        toc_chapter_count: Optional[int] = None
        if self.course_metadata is not None and getattr(
            self.course_metadata, "toc", None
        ):
            toc_chapter_count = len(self.course_metadata.toc)
            logger.info(
                f"Segmenter: reference TOC chapter count from metadata = "
                f"{toc_chapter_count}."
            )

        def _within_tolerance(n: int) -> bool:
            return toc_chapter_count is None or abs(n - toc_chapter_count) <= 2

        raw_blocks: Optional[List[Tuple[Optional[str], str]]] = None
        page_count = 0

        # --- Tier 0: Metadata TOC page-number-driven segmentation ---
        meta_blocks, page_count = self._extract_toc_metadata()
        if meta_blocks:
            logger.info(
                f"Tier 0 (metadata TOC): {len(meta_blocks)} blocks — "
                "using metadata TOC-driven segmentation."
            )
            raw_blocks = meta_blocks
        else:
            # --- Tier 1: bookmark outline TOC ---
            toc_blocks, page_count = self._extract_toc()

            if toc_blocks and _within_tolerance(len(toc_blocks)):
                logger.info(
                    f"TOC found with {len(toc_blocks)} entries — using TOC-based segmentation."
                )
                raw_blocks = toc_blocks
            else:
                if toc_blocks and toc_chapter_count is not None:
                    logger.warning(
                        f"Tier 1 (bookmark outline) produced {len(toc_blocks)} blocks, "
                        f"metadata TOC has {toc_chapter_count} chapters "
                        f"(tolerance ±2). Falling through to UNIT markers."
                    )

                # --- Tier 2: UNIT content markers ---
                unit_blocks, page_count = self._extract_unit_markers()
                if unit_blocks and _within_tolerance(len(unit_blocks)):
                    logger.info(
                        f"UNIT markers found with {len(unit_blocks)} blocks — "
                        f"using marker-based segmentation."
                    )
                    raw_blocks = unit_blocks
                else:
                    if unit_blocks and toc_chapter_count is not None:
                        logger.warning(
                            f"Tier 2 (UNIT markers) produced {len(unit_blocks)} blocks, "
                            f"metadata TOC has {toc_chapter_count} chapters "
                            f"(tolerance ±2). Falling through to font-heuristic."
                        )
                    else:
                        logger.info(
                            "No usable TOC or UNIT markers — falling back to font-heuristic."
                        )

                    # --- Tier 3: font-heuristic (final tier; logs but does not fall through) ---
                    raw_blocks, page_count = self._extract_blocks_with_headers()
                    if (
                        toc_chapter_count is not None
                        and not _within_tolerance(len(raw_blocks))
                    ):
                        logger.warning(
                            f"Tier 3 (font-heuristic) produced {len(raw_blocks)} blocks, "
                            f"metadata TOC has {toc_chapter_count} chapters. "
                            f"Final segmentation may be inaccurate — manual review recommended."
                        )

        logger.info(f"PDF has {page_count} pages — merging short blocks...")

        # ADR-040: Track the index of the first chapter-like heading so that
        # preface-titled blocks appearing before it can be classified as
        # `preface` rather than `frontmatter`.
        self._first_chapter_block_index = self._find_first_chapter_block_index(
            raw_blocks or []
        )

        merged_blocks = self._merge_short_blocks(raw_blocks or [])

        segments = []
        segment_id = 1

        for block_idx, (heading, text) in enumerate(merged_blocks):
            # Fallback chunking if the block exceeds the soft word ceiling
            chunks = self._chunk_text(text)
            for chunk in chunks:
                if not chunk.strip():
                    continue
                seg_type = self._classify_segment(
                    heading, chunk, block_index=block_idx
                )
                segments.append(Segment(
                    segment_id=segment_id,
                    heading=heading,
                    text=chunk.strip(),
                    segment_type=seg_type,
                ))
                segment_id += 1

        logger.info(f"Generated {len(segments)} segments for {self.pdf_path.name}")
        return segments

    # ------------------------------------------------------------------
    # Preface / first-chapter position helper (ADR-040)
    # ------------------------------------------------------------------

    def _is_before_first_chapter(self, block_index: Optional[int]) -> bool:
        """Return True if `block_index` is unknown, or it comes before the
        first chapter-like block detected during `segment()`. Used by
        `_classify_segment()` (ADR-040) to distinguish a genuine preface from
        a mid-book section titled "Introduction".
        """
        if block_index is None:
            # Caller didn't pass an index (direct call in tests / legacy path)
            return True
        if self._first_chapter_block_index is None:
            # No chapter-like heading found anywhere — be permissive
            return True
        return block_index < self._first_chapter_block_index

    @staticmethod
    def _find_first_chapter_block_index(
        raw_blocks: List[Tuple[Optional[str], str]],
    ) -> Optional[int]:
        """Return the index of the first block whose heading looks like a
        chapter start (e.g. "Chapter 1", "Unit I", "1. Introduction").

        Used by `_classify_segment()` to decide whether a preface-titled block
        actually appears before chapter 1 (in which case it is a genuine
        `preface`) or is a mid-book section that happens to share the name.
        """
        _chapter_start_re = re.compile(
            r'^(chapter|module|unit|part|lesson)\s+[\dIVXivx]+|'
            r'^\d+[.)]\s+\S',
            re.IGNORECASE,
        )
        for idx, (heading, _text) in enumerate(raw_blocks):
            if heading and _chapter_start_re.match(heading.strip()):
                return idx
        return None

    # ------------------------------------------------------------------
    # Tier 0: Metadata-TOC-driven segmentation
    # ------------------------------------------------------------------

    def _extract_toc_metadata(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Tier 0: Metadata-TOC-driven segmentation.

        Uses course_metadata.toc chapter titles to locate each chapter's
        physical start page in the PDF. Does NOT rely on the page numbers from
        the metadata (they can be wrong); instead it does a forward title search
        that distinguishes actual chapter-start pages from running headers.

        Produces:
          - "Frontmatter" block  — pages 0 .. first_chapter − 1
          - One block per chapter — e.g. "Chapter 1: Compilation"
          - Trailing pages (references, appendices) are included in the last
            chapter block and will be classified by segment type downstream.

        Falls through (returns empty list) when:
          - course_metadata is None or its toc has < 2 entries
          - Fewer than 2 chapter start pages can be located in the PDF
        """
        if not self.course_metadata or not getattr(self.course_metadata, "toc", None):
            return [], 0

        valid_entries = [e for e in self.course_metadata.toc if e.title]
        if len(valid_entries) < 2:
            logger.debug("Metadata TOC: fewer than 2 titled entries — skipping Tier 0.")
            return [], 0

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page_count = len(pdf.pages)

                # Extract first 400 chars of every page once (heading area)
                page_heads: List[str] = []
                for pg in pdf.pages:
                    try:
                        page_heads.append((pg.extract_text() or "")[:400].lower())
                    except Exception:
                        page_heads.append("")

                # Identify TOC/Contents pages so we skip them during search
                contents_pages = self._detect_contents_pages(page_heads, page_count)
                search_from = (max(contents_pages) + 1) if contents_pages else 0
                logger.debug(
                    f"Metadata TOC: contents pages = {sorted(contents_pages)}, "
                    f"start searching from page {search_from}."
                )

                # Locate each chapter start page sequentially
                chapter_pages: List[Tuple] = []
                for entry in valid_entries:
                    found = self._find_chapter_start_page(
                        page_heads, entry, search_from, page_count, contents_pages
                    )
                    if found is not None:
                        chapter_pages.append((entry, found))
                        search_from = found + 1

                if len(chapter_pages) < 2:
                    logger.debug(
                        "Metadata TOC: fewer than 2 chapter start pages found "
                        "— skipping Tier 0."
                    )
                    return [], page_count

                # Compute median font size for code-block detection
                all_sizes: List[float] = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w["size"] for w in words if "size" in w])
                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
                header_threshold = median_size * 1.4

                blocks: List[Tuple[Optional[str], str]] = []

                # Frontmatter: everything before the first chapter
                first_chapter_page = chapter_pages[0][1]
                if first_chapter_page > 0:
                    fm_text = self._extract_page_range_text(
                        pdf, 0, first_chapter_page, header_threshold
                    )
                    if fm_text.strip():
                        blocks.append(("Frontmatter", fm_text))

                # One block per chapter
                for i, (entry, start_page) in enumerate(chapter_pages):
                    end_page = (
                        chapter_pages[i + 1][1]
                        if i + 1 < len(chapter_pages)
                        else page_count
                    )
                    chapter_text = self._extract_page_range_text(
                        pdf, start_page, end_page, header_threshold
                    )
                    if chapter_text.strip():
                        heading = (
                            f"Chapter {entry.chapter_number}: {entry.title}"
                            if entry.chapter_number
                            else entry.title
                        )
                        blocks.append((heading, chapter_text))

                fm_count = 1 if blocks and blocks[0][0] == "Frontmatter" else 0
                logger.info(
                    f"Metadata TOC segmentation: {len(blocks)} blocks "
                    f"({fm_count} frontmatter + {len(chapter_pages)} chapters)."
                )
                return blocks, page_count

        except Exception as e:
            logger.error(
                f"Metadata TOC extraction failed for {self.pdf_path.name}: {e}"
            )
            return [], 0

    def _detect_contents_pages(self, page_heads: List[str], page_count: int) -> set:
        """Return the set of physical page indices that are TOC/Contents pages.

        Detection: a page is a contents page if it has a 'Contents' heading in
        its first 3 lines AND at least 2 lines ending with a dotted leader +
        page number (e.g. '........... 17').  Only the first 20 pages are checked
        (contents sections are always near the front).
        """
        _contents_heading = re.compile(
            r"^\s*(table\s+of\s+)?contents?\s*$", re.IGNORECASE
        )
        _dotted_line = re.compile(r"\.{3,}\s*[\divxlcdm]+\s*$", re.IGNORECASE)

        result: set = set()
        in_contents = False

        for pg_idx in range(min(20, page_count)):
            text = page_heads[pg_idx]
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            heading_found = any(_contents_heading.match(ln) for ln in lines[:3])

            if heading_found:
                in_contents = True

            if in_contents:
                dotted = sum(1 for ln in lines if _dotted_line.search(ln))
                if heading_found or dotted >= 2:
                    result.add(pg_idx)
                else:
                    in_contents = False  # end of contents section

        return result

    def _find_chapter_start_page(
        self,
        page_heads: List[str],
        entry,
        start: int,
        end: int,
        skip_pages: set,
    ) -> Optional[int]:
        """Find the physical page where a chapter starts.

        Requires two conditions to be met simultaneously:
          1. At least 2 of the first 3 significant words (>2 chars) from the
             chapter title appear in the first 400 chars of the page.
          2. The page itself starts with a chapter-opening pattern:
               - "Chapter N …"   (explicit chapter keyword)
               - "N <word> …"    (numbered chapter without keyword, e.g. "1 Compilation")
             This distinguishes actual chapter-start pages from running headers
             like "74 Chapter 10. Condition variables 10.2 …" (which start with
             a bare page number, not the chapter keyword).

        Returns the 0-based physical page index, or None if not found.
        """
        title = entry.title or ""
        chapter_num = str(entry.chapter_number or "").strip()

        title_words = [w for w in title.lower().split() if len(w) > 2][:3]
        min_matches = max(1, min(2, len(title_words)))

        for pg in range(start, end):
            if pg in skip_pages:
                continue
            text = page_heads[pg]
            if not text.strip():
                continue

            # Condition 1: enough title words present
            if title_words and sum(1 for w in title_words if w in text) < min_matches:
                continue

            # Condition 2: page opens with a chapter-start pattern
            if chapter_num:
                is_chapter_start = bool(
                    re.match(
                        rf"^\s*chapter\s+{re.escape(chapter_num)}\b",
                        text,
                        re.IGNORECASE,
                    )
                    or re.match(
                        rf"^\s*{re.escape(chapter_num)}\s+\S",
                        text,
                        re.IGNORECASE,
                    )
                )
                if not is_chapter_start:
                    continue
            elif not title_words:
                # No chapter number and no usable title words — can't locate
                continue

            return pg

        return None

    # ------------------------------------------------------------------
    # TOC-based segmentation (ADR-023 — primary strategy)
    # ------------------------------------------------------------------

    def _extract_toc(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Use the PDF's bookmark outline to derive chapter-accurate segments.

        Each entry in the PDF outline provides a (title, page_number) pair.
        This method groups the page text between consecutive TOC entries into one
        raw block, producing chapter-aligned segments with zero boundary confusion.

        Text quality:
          - Same body-crop (top 10%, bottom 8%) as the font-heuristic path.
          - Same CID replacement, table annotation, figure annotation.
          - Same [CODE]/[/CODE] markers for monospace lines.
          - Blank line inserted between pages for readability.

        Returns:
          - (blocks, page_count) if TOC has ≥2 usable entries.
          - ([], page_count) if no usable TOC found (caller falls back to heuristic).
        """
        blocks: List[Tuple[Optional[str], str]] = []
        page_count = 0

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page_count = len(pdf.pages)

                # --- Read the PDF outline ---
                outline = []
                try:
                    raw_outline = pdf.doc.outline
                    if raw_outline:
                        outline = self._flatten_outline(raw_outline, pdf)
                except Exception as e:
                    logger.debug(f"Could not read PDF outline: {e}")

                if len(outline) < 2:
                    logger.debug(
                        f"PDF outline has {len(outline)} entry/entries — too few for TOC segmentation."
                    )
                    return [], page_count

                logger.info(
                    f"PDF outline has {len(outline)} entries. "
                    f"Using TOC-based segmentation."
                )

                # Determine per-page median font size for code-block detection
                all_sizes = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])
                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
                header_threshold = median_size * 1.4

                # Build page-range segments from TOC entries
                # outline is a list of (title, 0-based-page-index)
                for i, (title, start_page) in enumerate(outline):
                    end_page = outline[i + 1][1] if i + 1 < len(outline) else page_count
                    # Clamp to valid range
                    start_page = max(0, min(start_page, page_count - 1))
                    end_page   = max(start_page + 1, min(end_page, page_count))

                    page_texts = []
                    _ocr_warned = False
                    for pg_idx in range(start_page, end_page):
                        page = pdf.pages[pg_idx]
                        W = float(page.width)
                        H = float(page.height)
                        body = page.within_bbox((0, H * 0.10, W, H * 0.92))

                        raw_words = body.extract_words(extra_attrs=["size", "fontname"])
                        if not raw_words:
                            # OCR fallback for scanned pages
                            if _check_ocr_available():
                                if not _ocr_warned:
                                    logger.info(
                                        f"[TOC-OCR] Scanned pages in '{self.pdf_path.name}' — "
                                        "using Tesseract fallback."
                                    )
                                    _ocr_warned = True
                                ocr_text = _ocr_page(self.pdf_path, pg_idx)
                                if ocr_text:
                                    clean_lines = [
                                        ln for ln in ocr_text.splitlines()
                                        if ln.strip() and not re.fullmatch(r'\d+', ln.strip())
                                    ]
                                    page_texts.extend(clean_lines)
                            continue

                        # Table detection (same as font-heuristic path)
                        table_bboxes = []
                        table_annotations = []
                        try:
                            tables = body.find_tables()
                            for table in tables:
                                bbox = table.bbox
                                table_bboxes.append(bbox)
                                try:
                                    extracted = table.extract()
                                    if extracted:
                                        table_text = "\n".join(
                                            " | ".join(
                                                str(cell).strip() if cell is not None else ''
                                                for cell in row
                                            )
                                            for row in extracted
                                            if any(cell is not None for cell in row)
                                        )
                                        annotation = (
                                            f"[TABLE:\n{table_text[:4000]}\n]"
                                            if table_text.strip()
                                            else "[TABLE]"
                                        )
                                    else:
                                        annotation = "[TABLE]"
                                except Exception:
                                    annotation = "[TABLE]"
                                table_annotations.append((bbox[1], annotation))
                        except Exception:
                            pass

                        # Filter out words inside table bboxes
                        filtered_words = [
                            w for w in raw_words
                            if not any(
                                w['x0'] >= tx0 and w['x1'] <= tx1
                                and w['top'] >= ttop and w['bottom'] <= tbot
                                for (tx0, ttop, tx1, tbot) in table_bboxes
                            )
                        ]

                        lines = self._words_to_lines(filtered_words, header_threshold)

                        # Inject table annotations at correct Y positions and build page text
                        in_code_block = False
                        line_iter = iter(lines)
                        page_line_texts = []

                        for ann_y, ann_text in sorted(table_annotations, key=lambda x: x[0]):
                            page_line_texts.append(ann_text)

                        # Simple linear pass: just reconstruct text with code markers
                        for line in lines:
                            text = line['text'].strip()
                            if not text or re.fullmatch(r'\d+', text):
                                continue
                            # Figure caption injection (Issue 6)
                            fig_match = re.match(
                                r'(Fig\.?\s*\d+\.\d+)\s+(.+)', text, re.IGNORECASE
                            )
                            if fig_match and not line['is_code'] and line['max_size'] < header_threshold:
                                page_line_texts.append(
                                    f"[FIGURE {fig_match.group(1)}: {fig_match.group(2).strip()}]"
                                )
                                continue
                            # Code block markers
                            if line['is_code'] and not in_code_block:
                                page_line_texts.append("[CODE]")
                                in_code_block = True
                            elif not line['is_code'] and in_code_block:
                                page_line_texts.append("[/CODE]")
                                in_code_block = False
                            page_line_texts.append(text)

                        if in_code_block:
                            page_line_texts.append("[/CODE]")
                            in_code_block = False

                        if page_line_texts:
                            page_texts.append("\n".join(page_line_texts))

                    combined_text = "\n\n".join(page_texts).strip()
                    if combined_text:
                        blocks.append((title.strip() if title else None, combined_text))

        except Exception as e:
            logger.error(f"Error reading PDF for TOC extraction: {e}")
            return [], page_count

        return blocks, page_count

    def _flatten_outline(self, outline, pdf) -> List[Tuple[Optional[str], int]]:
        """Recursively flatten the PDF bookmark tree into [(title, 0-based-page-num)].

        pdfplumber exposes the raw PyMuPDF/pikepdf outline as nested dicts with
        'title' and 'page' fields. This method walks the tree depth-first and
        returns a deduplicated, sorted flat list of (title, page_index) tuples.
        """
        result = []

        def _walk(items):
            for item in items:
                if isinstance(item, dict):
                    title = item.get('title', '') or ''
                    page  = item.get('page', None)
                    if page is not None:
                        # pdfplumber may return 0-based or 1-based depending on PDF version
                        try:
                            page_idx = int(page)
                            # Normalise: if clearly 1-based (page 0 would be before page 1),
                            # convert to 0-based.
                            if page_idx >= len(pdf.pages):
                                page_idx = len(pdf.pages) - 1
                            result.append((title.strip(), page_idx))
                        except (TypeError, ValueError):
                            pass
                    # Recurse into children
                    children = item.get('children', []) or []
                    _walk(children)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    # Some versions expose as (title, page) tuples
                    try:
                        result.append((str(item[0]).strip(), int(item[1])))
                    except (TypeError, ValueError):
                        pass

        _walk(outline)

        # Deduplicate consecutive same-page entries and sort by page
        result.sort(key=lambda x: x[1])
        seen_pages = set()
        deduped = []
        for title, page in result:
            if page not in seen_pages:
                seen_pages.add(page)
                deduped.append((title, page))

        return deduped

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Tier 2: UNIT content marker scan
    # ------------------------------------------------------------------

    def _extract_unit_markers(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Scan ALL PDF pages for explicit UNIT markers to derive segment boundaries.

        Searches each page's first ~200 characters for patterns like:
          'UNIT – I', 'UNIT-1', 'UNIT I', 'UNIT – IV'

        This covers university lecture notes (MRCET, JNTU, etc.) that have no
        PDF bookmarks but use explicit UNIT markers at section starts.
        Unlike the old visual TOC scanner (which only looked at pages 2-8 for
        a printed contents table), this scans the entire document for live
        markers, making it robust to PDFs where each unit starts mid-document.

        Returns:
            (blocks, page_count) if ≥2 UNIT markers found with monotonic pages.
            ([], page_count) otherwise — caller falls through to font-heuristic.
        """
        _UNIT_MARKER_RE = re.compile(r'UNIT\s*[-\u2013]?\s*([IVX]+|\d+)', re.IGNORECASE)
        _ROMAN_MAP = {
            'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
            'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        }

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page_count = len(pdf.pages)

                # Pass 1: Find pages with UNIT markers near the top
                unit_pages = []
                for pg_idx in range(page_count):
                    text = (pdf.pages[pg_idx].extract_text() or '')[:300]
                    m = _UNIT_MARKER_RE.search(text[:200])
                    if not m:
                        continue

                    unit_id = m.group(1).strip().upper()
                    if unit_id in _ROMAN_MAP:
                        unit_num = _ROMAN_MAP[unit_id]
                    elif unit_id.isdigit():
                        unit_num = int(unit_id)
                    else:
                        continue

                    # Title hint from text immediately after the marker
                    after = text[m.end():].strip()
                    title_line = after.split('\n')[0].strip()[:80]
                    title_line = re.sub(r'^[-\u2013:\s]+', '', title_line).strip()
                    unit_pages.append((unit_num, pg_idx, title_line))

                # Deduplicate: keep first occurrence of each unit number
                seen: set = set()
                deduped = []
                for unit_num, pg_idx, title in unit_pages:
                    if unit_num not in seen:
                        seen.add(unit_num)
                        deduped.append((unit_num, pg_idx, title))

                if len(deduped) < 2:
                    logger.debug(f"Found {len(deduped)} UNIT marker(s) — too few.")
                    return [], page_count

                # Validate monotonicity
                sorted_entries = sorted(deduped, key=lambda x: x[0])
                pages = [p for _, p, _ in sorted_entries]
                if not all(pages[i] < pages[i + 1] for i in range(len(pages) - 1)):
                    logger.warning("UNIT markers non-monotonic — skipping.")
                    return [], page_count

                logger.info(
                    f"Found {len(sorted_entries)} UNIT markers: "
                    + ", ".join(f"Unit {u} @ p.{p+1}" for u, p, _ in sorted_entries)
                )

                # Compute median font size for code-block detection
                all_sizes: List[float] = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])
                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
                header_threshold = median_size * 1.4

                blocks: List[Tuple[Optional[str], str]] = []

                # Frontmatter (pages before first unit)
                first_unit_page = sorted_entries[0][1]
                if first_unit_page > 0:
                    fm_text = self._extract_page_range_text(
                        pdf, 0, first_unit_page, header_threshold
                    )
                    if fm_text.strip():
                        blocks.append(("Frontmatter", fm_text))

                # Each unit
                for i, (unit_num, start_page, title) in enumerate(sorted_entries):
                    end_page = (
                        sorted_entries[i + 1][1]
                        if i + 1 < len(sorted_entries)
                        else page_count
                    )
                    unit_text = self._extract_page_range_text(
                        pdf, start_page, end_page, header_threshold
                    )
                    if unit_text.strip():
                        heading = f"Unit {unit_num}: {title}" if title else f"Unit {unit_num}"
                        blocks.append((heading, unit_text))

                return blocks, page_count

        except Exception as e:
            logger.error(f"Error in UNIT marker extraction: {e}")
            return [], page_count

    # ------------------------------------------------------------------
    # Shared: Extract and annotate text from a page range
    # ------------------------------------------------------------------

    def _extract_page_range_text(
        self, pdf, start_page: int, end_page: int, header_threshold: float
    ) -> str:
        """Extract clean, annotated text from a contiguous range of PDF pages.

        Shared by Tier 1 (TOC) and Tier 2 (UNIT markers). Applies:
        - Body crop (top 10%, bottom 8%)
        - Table detection + [TABLE: ...] annotation
        - Word reconstruction + CID replacement
        - [CODE]/[/CODE] markers for monospace blocks
        - [FIGURE X.Y: caption] markers
        - Page-number-only line suppression
        """
        page_texts = []

        for pg_idx in range(start_page, end_page):
            page = pdf.pages[pg_idx]
            W = float(page.width)
            H = float(page.height)
            body = page.within_bbox((0, H * 0.10, W, H * 0.92))

            raw_words = body.extract_words(extra_attrs=["size", "fontname"])
            if not raw_words:
                if _check_ocr_available():
                    ocr_text = _ocr_page(self.pdf_path, pg_idx)
                    if ocr_text:
                        clean_lines = [
                            ln for ln in ocr_text.splitlines()
                            if ln.strip() and not re.fullmatch(r'\d+', ln.strip())
                        ]
                        if clean_lines:
                            page_texts.append("\n".join(clean_lines))
                continue

            # Table detection
            table_bboxes = []
            table_annotations = []
            try:
                tables = body.find_tables()
                for table in tables:
                    bbox = table.bbox
                    table_bboxes.append(bbox)
                    try:
                        extracted = table.extract()
                        if extracted:
                            table_text = "\n".join(
                                " | ".join(
                                    str(cell).strip() if cell is not None else ''
                                    for cell in row
                                )
                                for row in extracted
                                if any(cell is not None for cell in row)
                            )
                            annotation = (
                                f"[TABLE:\n{table_text[:4000]}\n]"
                                if table_text.strip() else "[TABLE]"
                            )
                        else:
                            annotation = "[TABLE]"
                    except Exception:
                        annotation = "[TABLE]"
                    table_annotations.append(annotation)
            except Exception:
                pass

            filtered_words = [
                w for w in raw_words
                if not any(
                    w['x0'] >= tx0 and w['x1'] <= tx1
                    and w['top'] >= ttop and w['bottom'] <= tbot
                    for (tx0, ttop, tx1, tbot) in table_bboxes
                )
            ]

            lines = self._words_to_lines(filtered_words, header_threshold)

            page_line_texts: List[str] = []
            in_code_block = False

            for ann_text in table_annotations:
                page_line_texts.append(ann_text)

            for line in lines:
                text = line['text'].strip()
                if not text or re.fullmatch(r'\d+', text):
                    continue

                # Figure caption annotation
                fig_match = re.match(
                    r'(Fig\.?\s*\d+\.\d+)\s+(.+)', text, re.IGNORECASE
                )
                if fig_match and not line['is_code'] and line['max_size'] < header_threshold:
                    page_line_texts.append(
                        f"[FIGURE {fig_match.group(1)}: {fig_match.group(2).strip()}]"
                    )
                    continue

                if line['is_code'] and not in_code_block:
                    page_line_texts.append("[CODE]")
                    in_code_block = True
                elif not line['is_code'] and in_code_block:
                    page_line_texts.append("[/CODE]")
                    in_code_block = False

                page_line_texts.append(text)

            if in_code_block:
                page_line_texts.append("[/CODE]")

            if page_line_texts:
                page_texts.append("\n".join(page_line_texts))

        return "\n\n".join(page_texts).strip()

    # ------------------------------------------------------------------
    # Segment type classification (critic.md Issue 8)
    # ------------------------------------------------------------------

    def _classify_segment(
        self,
        heading: Optional[str],
        text: str,
        block_index: Optional[int] = None,
    ) -> str:
        """Classify a segment into one of:

        - ``instructional``   : Main content that should be scored by the Module Gate.
        - ``preface``         : Preface/foreword/introduction-before-chapter-1 — routed
          into Course Gate context by `evaluator.py`, not Module Gate (ADR-040).
        - ``exercise``        : Practice problems / coding challenges — bypassed.
        - ``solution``        : Model answers — bypassed.
        - ``reference_table`` : Appendices, index pages — bypassed. Only assigned when
          prose density ≤ 0.60 (ADR-040).
        - ``frontmatter``     : TOC, copyright, dedication — bypassed.
        - ``glossary``        : Key-terms glossary — bypassed (critic.v2 Issue 2).
        - ``summary``         : Chapter/module summary bullets — bypassed (critic.v2 Issue 2).
        - ``assessment``      : Review/check-your-understanding questions — bypassed (critic.v2 Issue 2).
        """
        # critic.v2.md Issue 1 — reject known publisher running headers BEFORE
        # any pattern matching fires so they never trigger frontmatter.
        heading_l = (heading or "").lower().strip()
        if heading_l in _KNOWN_RUNNING_HEADERS:
            heading = None
            heading_l = ""

        # ADR-040: Preface detection runs BEFORE frontmatter/reference checks.
        # A block titled "Preface" / "Foreword" / "Introduction" is a genuine
        # preface only if it appears before the first detected chapter.
        if heading_l and _PREFACE_HEADING_RE.match(heading_l):
            if self._is_before_first_chapter(block_index):
                return "preface"

        # critic.v4 Issue 2: Tier 0 assigns the heading "Frontmatter" to pages
        # before Chapter 1. If that block reads like a course/book introduction
        # (preface signals in body text), classify it as "preface" rather than
        # "frontmatter" so the Course Gate receives the structural context.
        if heading_l == "frontmatter" and self._is_before_first_chapter(block_index):
            _PREFACE_BODY_SIGNALS = (
                "this book", "this course", "this text", "the reader",
                "chapter by chapter", "how to use", "who should read",
                "what you will learn", "welcome", "purpose of this",
            )
            if sum(1 for phrase in _PREFACE_BODY_SIGNALS if phrase in text.lower()) >= 2:
                return "preface"

        # Reference table — two independent paths (ADR-040):
        #
        # Path 1 (heading-authoritative): an explicit heading like "Appendix A",
        # "Index", "Reference Table", or "Table of Contents" is always a
        # reference_table regardless of prose density. The heading signal is
        # reliable enough that the density check would only produce false
        # negatives (e.g. a TOC page with normal prose density).
        for pat in _REFERENCE_TABLE_PATTERNS:
            if pat.search(heading_l):
                return "reference_table"

        # Path 2 (body-driven, ADR-040): only fire when the body has ≥2
        # [TABLE:] markers AND prose density is ≤ 75%. Threshold raised from
        # 0.60 (critic.v4 Issue 2) — a genuine reference table is mostly
        # non-prose; 40% plain text was too loose and caught instructional
        # chapters that happen to contain several syntax tables.
        prose_density = _compute_prose_density(text)
        if text.count("[TABLE:") >= 2 and prose_density <= 0.75:
            return "reference_table"

        # Solution: heading says answer/solution
        for pat in _SOLUTION_PATTERNS:
            if pat.match(heading_l) or pat.match(text.lstrip()[:60]):
                return "solution"

        # Exercise: count lines that look like numbered problems
        # ADR-034 (critic.v3 Issue 3): exclude lines inside [CODE] blocks from
        # exercise matching to prevent false positives on code examples.
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        in_code = False
        exercise_line_count = 0
        for line in lines:
            if line == '[CODE]':
                in_code = True
                continue
            if line == '[/CODE]':
                in_code = False
                continue
            if not in_code and any(pat.match(line) for pat in _EXERCISE_PATTERNS):
                exercise_line_count += 1

        # ADR-034: heading-based exercise check:
        # 1. Numbered heading + exercise keyword (e.g. "1. Exercise on loops")
        # 2. Body exercise patterns applied to heading (e.g. "Exercise 3", "Practice 1")
        heading_is_exercise = (
            bool(_EXERCISE_HEADING_PATTERN.match(heading_l))
            or any(pat.match(heading_l) for pat in _EXERCISE_PATTERNS)
        )

        if heading_is_exercise or (exercise_line_count >= 3 and exercise_line_count >= len(lines) * 0.4):
            return "exercise"

        # critic.v2.md Issue 2a — Glossary heading (e.g. "Key Terms", "Glossary")
        for pat in _GLOSSARY_HEADING_PATTERNS:
            if pat.match(heading_l):
                return "glossary"

        # Glossary body fallback (critic.v4 Issue 2): scan the full segment
        # for definition-list structure rather than probing only the first 200
        # chars for the exact phrase "key terms".
        # A definition line looks like: "Term: explanation" or "Term — explanation"
        _DEFINITION_LINE_RE = re.compile(
            r'^\s*\*{0,2}[A-Z][^:—\n]{2,40}[:\u2014\u2013]\s+\S', re.MULTILINE
        )
        if len(_DEFINITION_LINE_RE.findall(text)) >= 5:
            return "glossary"

        # critic.v2.md Issue 2b — Summary heading
        for pat in _SUMMARY_HEADING_PATTERNS:
            if pat.match(heading_l):
                return "summary"

        # critic.v2.md Issue 2c — Assessment body: numbered questions with a/b/c/d options.
        # Require at least 2 numbered question lines AND at least 3 lettered option lines.
        option_lines = sum(1 for ln in lines if _ASSESSMENT_OPTION_RE.match(ln))
        question_lines = sum(1 for ln in lines if _NUMBERED_QUESTION_RE.match(ln))
        if option_lines >= 3 and question_lines >= 2:
            return "assessment"

        # Frontmatter / Metadata (TOC, Preface, History, etc.)
        for pat in _FRONTMATTER_PATTERNS:
            if pat.search(heading_l):
                return "frontmatter"

        # Check first line of text for frontmatter as fallback if heading was missed
        first_line = text.lstrip().split('\n')[0].strip().lower()
        for pat in _FRONTMATTER_PATTERNS:
            if pat.fullmatch(first_line):
                return "frontmatter"

        # ADR-034 (critic.v3 Issue 4): institutional boilerplate in body text
        # Check front portion of segment for institutional markers
        front_500 = text[:500].lower()
        if re.search(r'(autonomous\s+institution|accredited\s+by|affiliated\s+to|UGC|AICTE|JNTU)', front_500, re.IGNORECASE):
            return "frontmatter"
        # Syllabus listing: contains UNIT markers AND textbook references
        if (re.search(r'UNIT\s+[IVX\d]+', text[:2000], re.IGNORECASE)
                and re.search(r'(text\s*book|reference\s*book|recommended\s+reading)', text[:2000], re.IGNORECASE)):
            return "frontmatter"

        # Copyright / license pages (content-based heuristic)
        if _is_copyright_page(text):
            return "frontmatter"

        return "instructional"

    # ------------------------------------------------------------------
    # Line reconstruction helpers
    # ------------------------------------------------------------------

    def _words_to_lines(self, words: list, header_threshold: float) -> list:
        """Group word objects by approximate Y baseline into reconstructed lines.

        Joining word objects with explicit spaces (rather than relying on the character
        stream) correctly reconstructs word boundaries in ligature-encoded PDFs.
        Strips standalone oversized-font digits (Springer chapter-number marginalia).
        Replaces CID artifacts with [?] (critic.md Issue 9).
        Returns list of dicts: {text, max_size, is_code}
        """
        if not words:
            return []
        line_groups: dict = {}
        for w in words:
            key = round(w['doctop'] / 2) * 2   # 2pt vertical tolerance
            line_groups.setdefault(key, []).append(w)

        result = []
        for y in sorted(line_groups):
            line_words = sorted(line_groups[y], key=lambda w: w['x0'])
            # Strip standalone oversized-font digits (margin chapter numbers)
            line_words = [
                w for w in line_words
                if not (re.fullmatch(r'\d+', w['text']) and w.get('size', 0) >= header_threshold)
            ]
            if not line_words:
                continue
            text = ' '.join(w['text'] for w in line_words)
            # Critic Issue 9: replace CID artifacts
            text = _CID_RE.sub('[?]', text)
            max_size = max(w.get('size', 0) for w in line_words)
            # Detect monospace code lines (Courier, Consolas, Monaco, etc.)
            is_code = any(
                'Courier' in str(w.get('fontname', '')) or
                'Mono' in str(w.get('fontname', '')) or
                'Code' in str(w.get('fontname', ''))
                for w in line_words
            )
            # Detect bold fonts for header detection heuristics
            is_bold = any(
                'Bold' in str(w.get('fontname', '')) or
                'Black' in str(w.get('fontname', '')) or
                'Heavy' in str(w.get('fontname', ''))
                for w in line_words
            )
            result.append({'text': text, 'max_size': max_size, 'is_code': is_code, 'is_bold': is_bold})
        return result

    def _extract_blocks_with_headers(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Extract text from PDF and identify major section headers by significant font-size jump.

        Crops each page to body region (top 10%, bottom 8%) to strip running headers/footers.
        Detects tables via pdfplumber and annotates them as [TABLE: ...] markers.
        Uses word-level reconstruction (via _words_to_lines) to handle ligature-encoded fonts.
        Annotates consecutive monospace lines with [CODE]/[/CODE] markers.
        Extracts figure captions and annotates as [FIGURE X.Y: caption] (Issue 6).
        Replaces (cid:N) artifacts with [?] (Issue 9).

        Only triggers a new block when the line's max font size is >= 1.4x the body median
        AND the line is short enough to be a title (< 80 chars).

        Returns (blocks, page_count).
        """
        blocks = []
        current_heading: Optional[str] = None
        current_text_lines: List[str] = []
        page_count = 0

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page_count = len(pdf.pages)

                # 1. Determine median font size across ALL pages for a stable baseline
                all_sizes = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])

                if not all_sizes:
                    logger.warning(f"Could not detect text natively in {self.pdf_path.name}. Will use OCR if available.")
                    # Don't return early — let the per-page loop try OCR
                    median_size = 10.0  # sensible default when no native text exists
                    header_threshold = median_size * 1.4
                else:
                    all_sizes.sort()
                    median_size = all_sizes[len(all_sizes) // 2]
                    header_threshold = median_size * 1.4
                    logger.info(f"Body median font size: {median_size:.1f}pt — chapter header threshold: {header_threshold:.1f}pt")

                # 2. Extract and group lines per page
                _ocr_warned = False
                for page in pdf.pages:
                    W = float(page.width)
                    H = float(page.height)

                    # Crop to body region — strips running headers (top 10%) and footers (bottom 8%)
                    body = page.within_bbox((0, H * 0.10, W, H * 0.92))

                    # --- OCR FALLBACK: if page has no native text, try tesseract ---
                    raw_words_check = body.extract_words(extra_attrs=["size", "fontname"])
                    if not raw_words_check:
                        if _check_ocr_available():
                            if not _ocr_warned:
                                logger.info(f"[OCR] Scanned pages detected in {self.pdf_path.name} — using Tesseract OCR fallback.")
                                _ocr_warned = True
                            ocr_text = _ocr_page(self.pdf_path, page.page_number - 1)
                            if ocr_text:
                                # Strip common OCR noise: lines that are just digits (page numbers)
                                clean_lines = [
                                    ln for ln in ocr_text.splitlines()
                                    if ln.strip() and not re.fullmatch(r'\d+', ln.strip())
                                ]
                                current_text_lines.extend(clean_lines)
                        else:
                            logger.debug(f"[OCR] Page {page.page_number} has no native text and tesseract is not available — skipping.")
                        continue

                    # Detect tables in the body region; collect bounding boxes
                    table_bboxes = []
                    table_annotations = []  # list of (y_pos, annotation_text)
                    try:
                        tables = body.find_tables()
                        for table in tables:
                            bbox = table.bbox  # (x0, top, x1, bottom)
                            table_bboxes.append(bbox)
                            # Build annotation from all rows of extracted data
                            try:
                                extracted = table.extract()
                                if extracted:
                                    table_text = "\n".join(
                                        " | ".join(str(cell).strip() if cell is not None else '' for cell in row)
                                        for row in extracted if any(cell is not None for cell in row)
                                    )
                                    if table_text.strip():
                                        annotation = f"[TABLE:\n{table_text}\n]"
                                        # Truncate if it exceeds reasonable context window size
                                        if len(annotation) > 4000:
                                            annotation = annotation[:4000] + "\n...]"
                                    else:
                                        annotation = "[TABLE]"
                                else:
                                    annotation = "[TABLE]"
                            except Exception:
                                annotation = "[TABLE]"
                            table_annotations.append((bbox[1], annotation))  # top Y of table
                    except Exception:
                        pass

                    # Extract words from body, skipping those inside table bounding boxes
                    raw_words = body.extract_words(extra_attrs=["size", "fontname"])
                    filtered_words = []
                    for w in raw_words:
                        in_table = False
                        for (tx0, ttop, tx1, tbot) in table_bboxes:
                            if w['x0'] >= tx0 and w['x1'] <= tx1 and w['top'] >= ttop and w['bottom'] <= tbot:
                                in_table = True
                                break
                        if not in_table:
                            filtered_words.append(w)

                    # Reconstruct lines from word objects
                    lines = self._words_to_lines(filtered_words, header_threshold)

                    # Build a merged sequence: lines + table annotations, sorted by Y
                    line_entries = []  # (y, type, data)
                    if filtered_words:
                        line_groups: dict = {}
                        for w in filtered_words:
                            key = round(w['doctop'] / 2) * 2
                            line_groups.setdefault(key, []).append(w)
                        for y in sorted(line_groups):
                            line_entries.append((y, 'line', None))

                    for (ty, annotation) in table_annotations:
                        page_offset = page.doctop if hasattr(page, 'doctop') else 0
                        line_entries.append((ty + page_offset, 'table', annotation))

                    line_entries.sort(key=lambda e: e[0])

                    # Process in Y order
                    line_iter = iter(lines)
                    in_code_block = False

                    for (y, entry_type, data) in line_entries:
                        if entry_type == 'table':
                            if in_code_block:
                                current_text_lines.append("[/CODE]")
                                in_code_block = False
                            current_text_lines.append(data)
                            continue

                        # entry_type == 'line'
                        try:
                            line = next(line_iter)
                        except StopIteration:
                            break

                        text = line['text'].strip()
                        if not text:
                            continue

                        max_size = line['max_size']
                        is_code = line['is_code']

                        # Skip pure page numbers
                        if re.fullmatch(r'\d+', text):
                            continue

                        # Critic Issue 6: figure caption injection
                        # Detect lines that ARE figure captions (e.g. "Fig. 1.1 The Python interpreter")
                        fig_caption_match = re.match(
                            r'(Fig\.?\s*\d+\.\d+)\s+(.+)', text, re.IGNORECASE
                        )
                        if fig_caption_match and not is_code and max_size < header_threshold:
                            fig_id = fig_caption_match.group(1)
                            caption = fig_caption_match.group(2).strip()
                            current_text_lines.append(f"[FIGURE {fig_id}: {caption}]")
                            continue

                        # Handle CODE block transitions
                        if is_code and not in_code_block:
                            current_text_lines.append("[CODE]")
                            in_code_block = True
                        elif not is_code and in_code_block:
                            current_text_lines.append("[/CODE]")
                            in_code_block = False

                        is_bold = line.get('is_bold', False)

                        # ADR-029 (critic.v3 Issue 1): Bold-frequency filter.
                        # Bold text qualifies as a header ONLY if:
                        #  - bold_as_header is enabled
                        #  - the text is not a high-frequency label
                        #  - the text does not end with ':' (label pattern)
                        #  - the text is not a common code label word
                        bold_ok = False
                        if self.bold_as_header and is_bold:
                            text_lower = text.lower().rstrip('.,:;')
                            if (text_lower not in _BOLD_LABEL_EXCLUSIONS
                                    and not text.endswith(':')
                                    and text_lower not in _KNOWN_RUNNING_HEADERS):
                                bold_ok = True

                        # Chapter-level header: significant size jump OR qualified bold, AND short enough to be a title
                        is_header = (len(text) < 80 and (max_size >= header_threshold or bold_ok))

                        if is_header:
                            if in_code_block:
                                current_text_lines.append("[/CODE]")
                                in_code_block = False
                            # Save previous block before starting a new section
                            if current_text_lines:
                                blocks.append((current_heading, "\n".join(current_text_lines)))
                                current_text_lines = []
                            current_heading = text
                        else:
                            current_text_lines.append(text)

                    # Close any open code block at end of page
                    if in_code_block:
                        current_text_lines.append("[/CODE]")
                        in_code_block = False

            # Append the final block
            if current_text_lines:
                blocks.append((current_heading, "\n".join(current_text_lines)))

        except Exception as e:
            logger.error(f"Error reading PDF {self.pdf_path}: {e}")

        return blocks, page_count

    def _merge_short_blocks(
        self,
        blocks: List[Tuple[Optional[str], str]]
    ) -> List[Tuple[Optional[str], str]]:
        """Greedily merge adjacent block pairs until no adjacent pair fits
        under `max_words` (ADR-037).

        At each step merges the pair whose combined word count is smallest,
        provided the combined total does not exceed `self.max_words`. Prevents
        merging across strong chapter boundaries.
        """
        blocks = list(blocks)  # copy
        while len(blocks) > 1:
            mergeable = []
            _unit_boundary_re = re.compile(r'UNIT\s+[–\-]?\s*[IVX\d]+', re.IGNORECASE)
            for i in range(len(blocks) - 1):
                h0, t0 = blocks[i]
                h1, t1 = blocks[i + 1]
                if _word_count(t0) + _word_count(t1) < self.max_words:
                    # Avoid merging if the second block is a strong chapter start
                    if h1 and re.match(r'^(chapter|module|unit|part)\s+\d+', h1.strip(), re.IGNORECASE):
                        continue
                    # ADR-029 (critic.v3 Issue 12): Unit boundary merge barrier
                    if h1 and _unit_boundary_re.search(h1):
                        continue
                    if _unit_boundary_re.search(t1[:200]):
                        continue
                    mergeable.append(i)

            if not mergeable:
                break

            best_i = min(
                mergeable,
                key=lambda i: _word_count(blocks[i][1]) + _word_count(blocks[i + 1][1]),
            )
            h0, t0 = blocks[best_i]
            h1, t1 = blocks[best_i + 1]

            blocks[best_i] = (h0 if h0 else h1, t0 + "\n\n" + t1)
            blocks.pop(best_i + 1)

        logger.debug(f"Block merge: resulted in {len(blocks)} blocks")
        return blocks

    def _chunk_text(self, text: str) -> List[str]:
        """Split text that exceeds `max_words` safely by paragraph → sentence
        → word boundaries (ADR-037).

        Under normal operation this is a no-op: `max_words=30000` is large
        enough that almost every chapter passes through untouched. It only
        triggers on pathological megachapters (>30k words).
        """
        if _word_count(text) <= self.max_words:
            return [text]

        chunks: List[str] = []
        paragraphs = re.split(r'\n\n+', text)

        current_chunk = ""
        current_words = 0

        def _flush():
            nonlocal current_chunk, current_words
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = ""
            current_words = 0

        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            p_words = _word_count(p)

            if p_words > self.max_words:
                # Paragraph itself exceeds ceiling — split at sentence level
                sentences = re.split(r'(?<=[.!?])\s+', p)
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    s_words = _word_count(sentence)

                    if s_words > self.max_words:
                        # Single sentence exceeds ceiling — hard split at word boundaries
                        _flush()
                        tokens = sentence.split()
                        for i in range(0, len(tokens), self.max_words):
                            chunks.append(" ".join(tokens[i : i + self.max_words]))
                        continue

                    if current_words + s_words + 1 > self.max_words:
                        _flush()
                        current_chunk = sentence + " "
                        current_words = s_words
                    else:
                        current_chunk += sentence + " "
                        current_words += s_words

                if current_chunk:
                    current_chunk += "\n\n"
            else:
                if current_words + p_words + 2 > self.max_words:
                    _flush()
                    current_chunk = p + "\n\n"
                    current_words = p_words
                else:
                    current_chunk += p + "\n\n"
                    current_words += p_words

        _flush()
        return chunks
