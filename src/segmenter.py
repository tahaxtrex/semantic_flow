import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional
import pdfplumber
import re

from src.models import Segment

logger = logging.getLogger(__name__)

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
_EXERCISE_PATTERNS = [
    re.compile(r'^(\d+[\.\)]|Practice\s+\d|Exercise\s+\d|Q\d+[\.\)])', re.IGNORECASE),
    re.compile(r'\b(write a program|create a|implement|design a)\b', re.IGNORECASE),
]
_SOLUTION_PATTERNS = [
    re.compile(r'^(Solution|Answer|Answers|Solutions)\b', re.IGNORECASE),
    re.compile(r'^(Answer to|Solution to)\b', re.IGNORECASE),
]
_REFERENCE_TABLE_PATTERNS = [
    re.compile(r'\b(appendix|table of|reference table|index)\b', re.IGNORECASE),
]
_FRONTMATTER_PATTERNS = [
    re.compile(r'^(\s*(table of contents|contents|preface|acknowledgments|about this book|history|sources|foreword|dedication|bibliography|glossary|index|appendix|abbreviations|list of figures|list of tables)\s*)$', re.IGNORECASE),
    re.compile(r'^(about\s+\w[\w\s]+|coverage\s+and\s+scope|pedagogical\s+foundation)', re.IGNORECASE),
]

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
    re.compile(r'^(key\s+terms?|glossary)$', re.IGNORECASE),
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

    def __init__(self, pdf_path: Path, max_chars: int = 8000, min_chars: int = 600):
        self.pdf_path = Path(pdf_path)
        self.max_chars = max_chars
        self.min_chars = min_chars  # kept for backward compat; not used in main merge path

    def segment(self) -> List[Segment]:
        """Extract text from PDF, group by major headers, and apply fallback chunking.

        Extraction strategy (ADR-023, three-tier hierarchy):
          1. TOC path:  If the PDF has a bookmark outline with ≥2 entries, use the
             TOC to derive chapter-precise page ranges. Most accurate.
          2. Font-heuristic path: Fall back to font-size jump detection if no TOC.
          3. Sentence chunking: Applied to any block that exceeds max_chars.
        """
        logger.info(f"Segmenting PDF: {self.pdf_path.name}")

        # --- Try TOC-based extraction first (ADR-023) ---
        toc_blocks, page_count = self._extract_toc()
        if toc_blocks:
            logger.info(
                f"TOC found with {len(toc_blocks)} entries — using TOC-based segmentation."
            )
            raw_blocks = toc_blocks
        else:
            logger.info(
                f"No usable TOC — falling back to font-heuristic segmentation."
            )
            raw_blocks, page_count = self._extract_blocks_with_headers()

        logger.info(f"PDF has {page_count} pages — merging short blocks...")
        merged_blocks = self._merge_short_blocks(raw_blocks)

        segments = []
        segment_id = 1

        for heading, text in merged_blocks:
            # Fallback chunking if text block is too long
            chunks = self._chunk_text(text)
            for chunk in chunks:
                if not chunk.strip():
                    continue
                seg_type = self._classify_segment(heading, chunk)
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
    # Segment type classification (critic.md Issue 8)
    # ------------------------------------------------------------------

    def _classify_segment(self, heading: Optional[str], text: str) -> str:
        """Classify a segment into one of:

        - ``instructional``   : Main content that should be scored by the Module Gate.
        - ``exercise``        : Practice problems / coding challenges — bypassed.
        - ``solution``        : Model answers — bypassed.
        - ``reference_table`` : Appendices, index pages — bypassed.
        - ``frontmatter``     : TOC, preface, copyright — bypassed.
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

        # Reference table: heading matches appendix/table keywords
        for pat in _REFERENCE_TABLE_PATTERNS:
            if pat.search(heading_l):
                return "reference_table"

        # Solution: heading says answer/solution
        for pat in _SOLUTION_PATTERNS:
            if pat.match(heading_l) or pat.match(text.lstrip()[:60]):
                return "solution"

        # Exercise: count lines that look like numbered problems
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        exercise_line_count = sum(
            1 for line in lines
            if any(pat.match(line) for pat in _EXERCISE_PATTERNS)
        )
        # Also check heading
        heading_is_exercise = any(pat.match(heading_l) for pat in _EXERCISE_PATTERNS)

        if heading_is_exercise or (exercise_line_count >= 3 and exercise_line_count >= len(lines) * 0.4):
            return "exercise"

        # critic.v2.md Issue 2a — Glossary heading (e.g. "Key Terms", "Glossary")
        for pat in _GLOSSARY_HEADING_PATTERNS:
            if pat.match(heading_l):
                return "glossary"

        # Glossary body fallback
        if re.search(r'(^|\n)key\s+terms(\n|$)', text[:200], re.IGNORECASE):
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

                        # Chapter-level header: significant size jump OR bold, AND short enough to be a title
                        is_header = (len(text) < 80 and (max_size >= header_threshold or is_bold))

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
        """Greedily merge adjacent block pairs until no adjacent pair is under max_chars.

        At each step merges the pair whose combined text is shortest, provided the
        combined length does not exceed self.max_chars (avoids arbitrary page thresholds).
        Prevents merging across strong chapter boundaries.
        """
        blocks = list(blocks)  # copy
        while len(blocks) > 1:
            # Find mergeable pairs
            mergeable = []
            for i in range(len(blocks) - 1):
                h0, t0 = blocks[i]
                h1, t1 = blocks[i + 1]
                if len(t0) + len(t1) < self.max_chars:
                    # Avoid merging if the second block is a strong chapter start
                    if h1 and re.match(r'^(chapter|module|unit|part)\s+\d+', h1.strip(), re.IGNORECASE):
                        continue
                    mergeable.append(i)

            if not mergeable:
                break

            # Find adjacent pair with smallest combined text length among mergeable
            best_i = min(mergeable, key=lambda i: len(blocks[i][1]) + len(blocks[i+1][1]))
            h0, t0 = blocks[best_i]
            h1, t1 = blocks[best_i + 1]

            blocks[best_i] = (h0 if h0 else h1, t0 + "\n\n" + t1)
            blocks.pop(best_i + 1)
                
        logger.debug(f"Block merge: resulted in {len(blocks)} blocks")
        return blocks

    def _chunk_text(self, text: str) -> List[str]:
        """Split text that exceeds max_chars safely by paragraph and sentence boundaries."""
        if len(text) <= self.max_chars:
            return [text]

        chunks = []
        paragraphs = re.split(r'\n\n+', text)

        current_chunk = ""
        for p in paragraphs:
            p = p.strip()
            if not p: continue

            if len(p) > self.max_chars:
                sentences = re.split(r'(?<=[.!?])\s+', p)
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence: continue

                    if len(sentence) > self.max_chars:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                            current_chunk = ""
                        for i in range(0, len(sentence), self.max_chars):
                            chunks.append(sentence[i:i + self.max_chars])
                        continue

                    if len(current_chunk) + len(sentence) + 1 > self.max_chars:
                        chunks.append(current_chunk.strip())
                        current_chunk = sentence + " "
                    else:
                        current_chunk += sentence + " "
                
                if current_chunk:
                    current_chunk += "\n\n"
            else:
                if len(current_chunk) + len(p) + 2 > self.max_chars:
                    chunks.append(current_chunk.strip())
                    current_chunk = p + "\n\n"
                else:
                    current_chunk += p + "\n\n"

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks
