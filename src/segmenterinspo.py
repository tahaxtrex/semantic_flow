import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional
import pdfplumber
import re

logger = logging.getLogger(__name__)

# ── Segment model (inline to avoid import issues in test) ──
from pydantic import BaseModel, Field

class Segment(BaseModel):
    segment_id: int
    heading: Optional[str] = None
    text: str
    segment_type: str = "instructional"

# OCR availability flag
_OCR_AVAILABLE: Optional[bool] = None

def _check_ocr_available() -> bool:
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is None:
        try:
            import pytesseract
            import pdf2image
            pytesseract.get_tesseract_version()
            _OCR_AVAILABLE = True
        except Exception:
            _OCR_AVAILABLE = False
    return _OCR_AVAILABLE

def _ocr_page(pdf_path: Path, page_index: int) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), dpi=300, first_page=page_index+1, last_page=page_index+1)
        if not images:
            return ""
        return pytesseract.image_to_string(images[0], lang="eng").strip()
    except Exception as e:
        logger.warning(f"OCR failed for page {page_index + 1}: {e}")
        return ""

_CID_RE = re.compile(r'\(cid:\d+\)')
_FIG_REF_RE = re.compile(r'Fig\.?\s*(\d+\.\d+)\b', re.IGNORECASE)

# Segment type detection patterns
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
_KNOWN_RUNNING_HEADERS: frozenset = frozenset({
    "access. the future of education.",
    "access for free at openstax.org",
    "access for free at",
    "openstax",
})
_GLOSSARY_HEADING_PATTERNS = [
    re.compile(r'^(key\s+terms?|glossary)$', re.IGNORECASE),
]
_SUMMARY_HEADING_PATTERNS = [
    re.compile(r'^(summary|chapter\s+summary|module\s+summary|section\s+summary)$', re.IGNORECASE),
]
_ASSESSMENT_OPTION_RE = re.compile(r'^[a-d]\.\s+\S', re.IGNORECASE)
_NUMBERED_QUESTION_RE = re.compile(r'^\d+[.)\s]\s*\S')

_COPYRIGHT_KEYWORDS = [
    'creative commons', 'all rights reserved', 'isbn', 'doi:', 'published by',
    'reproduced without', 'prior written consent',
    'licensed under', 'licensing, please contact', 'rice university',
    'openstax', 'kendall hunt', 'arnold ventures', 'chan zuckerberg',
    'attribution', 'noncommercial',
]

# ── NEW: Institutional boilerplate keywords (critic_v3 Issue 4) ──
_INSTITUTIONAL_KEYWORDS = [
    'ugc', 'aicte', 'jntuh', 'affiliated to', 'accredited by', 'approved by',
    'autonomous institution', 'iso 9001', 'naac', 'nba',
]

def _is_copyright_page(text: str) -> bool:
    lower = text.lower()
    symbol_hits = lower.count('©')
    keyword_hits = sum(1 for kw in _COPYRIGHT_KEYWORDS if kw in lower)
    total_hits = keyword_hits + (2 if symbol_hits >= 1 else 0)
    return total_hits >= 4

def _is_institutional_boilerplate(text: str) -> bool:
    """Detect institutional/syllabus boilerplate (critic_v3 Issue 4)."""
    lower = text.lower()
    inst_hits = sum(1 for kw in _INSTITUTIONAL_KEYWORDS if kw in lower)
    has_syllabus = bool(re.search(r'unit\s+[ivx\d]+.*unit\s+[ivx\d]+', lower, re.DOTALL))
    has_textbooks = 'text book' in lower or 'reference book' in lower
    # 3+ institutional keywords, or institutional + syllabus/textbooks
    return inst_hits >= 3 or (inst_hits >= 1 and (has_syllabus or has_textbooks))


class SmartSegmenter:
    """Deterministically extracts and segments text from PDFs.

    Extraction strategy (4-tier hierarchy):
      1. PDF bookmark outline (most accurate)
      2. UNIT content marker scan (NEW — for university lecture notes)
      3. Font-heuristic with FIXED bold threshold
      4. Sentence chunking for oversized blocks
    """

    def __init__(self, pdf_path: Path, max_chars: int = 8000, min_chars: int = 600):
        self.pdf_path = Path(pdf_path)
        self.max_chars = max_chars
        self.min_chars = min_chars

    def segment(self) -> List[Segment]:
        """Extract text from PDF using the 4-tier hierarchy."""
        logger.info(f"Segmenting PDF: {self.pdf_path.name}")

        # --- Tier 1: PDF bookmark outline ---
        toc_blocks, page_count = self._extract_toc()
        if toc_blocks:
            logger.info(f"Tier 1 (bookmarks): {len(toc_blocks)} entries — using TOC segmentation.")
            raw_blocks = toc_blocks
        else:
            # --- Tier 2: UNIT content markers (NEW) ---
            unit_blocks, page_count = self._extract_unit_markers()
            if unit_blocks:
                logger.info(f"Tier 2 (UNIT markers): {len(unit_blocks)} blocks — using marker segmentation.")
                raw_blocks = unit_blocks
            else:
                # --- Tier 3: Font-heuristic (with bold fix) ---
                logger.info("No bookmarks or UNIT markers — falling back to font-heuristic.")
                raw_blocks, page_count = self._extract_blocks_with_headers()

        logger.info(f"PDF has {page_count} pages — merging short blocks...")
        merged_blocks = self._merge_short_blocks(raw_blocks)

        segments = []
        segment_id = 1
        for heading, text in merged_blocks:
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
    # Tier 1: TOC-based segmentation (unchanged)
    # ------------------------------------------------------------------

    def _extract_toc(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Tier 1: Use PDF bookmark outline for chapter-accurate segmentation.

        Uses pypdf for reliable bookmark resolution (pdfminer's pdf.doc.outline
        is unavailable in pdfplumber ≥0.10). Falls back gracefully if no
        bookmarks exist or pypdf is not installed.
        """
        blocks: List[Tuple[Optional[str], str]] = []
        page_count = 0
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page_count = len(pdf.pages)

                # Extract outline via pypdf (robust across PDF versions)
                outline = self._extract_outline_pypdf(page_count)

                if len(outline) < 2:
                    logger.debug(f"PDF outline has {len(outline)} entry/entries — too few.")
                    return [], page_count

                logger.info(f"PDF bookmark outline has {len(outline)} entries.")

                all_sizes = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])
                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
                header_threshold = median_size * 1.4

                for i, (title, start_page) in enumerate(outline):
                    end_page = outline[i + 1][1] if i + 1 < len(outline) else page_count
                    start_page = max(0, min(start_page, page_count - 1))
                    end_page = max(start_page + 1, min(end_page, page_count))

                    combined_text = self._extract_page_range_text(pdf, start_page, end_page, header_threshold)
                    if combined_text.strip():
                        blocks.append((title.strip() if title else None, combined_text))

        except Exception as e:
            logger.error(f"Error reading PDF for TOC extraction: {e}")
            return [], page_count
        return blocks, page_count

    def _extract_outline_pypdf(self, page_count: int) -> List[Tuple[str, int]]:
        """Extract bookmark outline using pypdf (handles all PDF bookmark formats).

        Includes a quality filter: rejects outlines that appear to be auto-generated
        garbage (code snippets as titles, many bookmarks on one page, etc.).

        Returns a flat, deduplicated, sorted list of (title, 0-based-page-index).
        Returns empty list if no bookmarks found, quality is too low, or pypdf unavailable.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            logger.debug("pypdf not installed — bookmark extraction unavailable.")
            return []

        try:
            reader = PdfReader(str(self.pdf_path))
            if not reader.outline:
                return []

            def _flatten(items):
                result = []
                for item in items:
                    if isinstance(item, list):
                        result.extend(_flatten(item))
                    else:
                        try:
                            title = item.title
                            page_idx = reader.get_destination_page_number(item)
                            if 0 <= page_idx < page_count and title.strip():
                                result.append((title.strip(), page_idx))
                        except Exception:
                            pass
                return result

            entries = _flatten(reader.outline)
            if not entries:
                return []

            # ── Quality filter: reject garbage outlines ──────────────────
            # Heuristic: if many bookmarks are clustered on one page, or if
            # titles look like code/output, the outline is auto-generated junk.
            from collections import Counter
            page_counts = Counter(p for _, p in entries)
            max_per_page = max(page_counts.values())

            code_indicators = re.compile(
                r'^[#\[\(]|'           # starts with #, [, (
                r'\s*=\s*|'            # contains assignment
                r'^\w+\s*\(|'          # function call: word(
                r'^(for|if|while|def|class|print|import)\s',  # Python keywords
                re.IGNORECASE
            )
            code_like = sum(1 for t, _ in entries if code_indicators.search(t))
            code_ratio = code_like / len(entries)

            if max_per_page >= 5:
                logger.info(
                    f"Bookmark outline rejected: {max_per_page} entries on one page "
                    f"(likely auto-generated). Falling through to next tier."
                )
                return []

            if code_ratio > 0.3:
                logger.info(
                    f"Bookmark outline rejected: {code_like}/{len(entries)} titles look like code "
                    f"({code_ratio:.0%}). Falling through to next tier."
                )
                return []

            # Sort by page and deduplicate
            entries.sort(key=lambda x: x[1])
            seen_pages = set()
            deduped = []
            for title, page in entries:
                if page not in seen_pages:
                    seen_pages.add(page)
                    deduped.append((title, page))
            return deduped

        except Exception as e:
            logger.debug(f"pypdf outline extraction failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Tier 2: UNIT content marker scan (NEW)
    # ------------------------------------------------------------------

    def _extract_unit_markers(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Scan PDF pages for UNIT content markers to derive segment boundaries.

        Searches each page's first ~200 characters for patterns like:
          'UNIT – I', 'UNIT-1', 'UNIT I', 'UNIT – IV'

        Covers university lecture notes (MRCET, JNTU, etc.) that have no
        PDF bookmarks but use explicit UNIT markers at section starts.

        Returns:
            (blocks, page_count) if ≥2 UNIT markers found with monotonic pages.
            ([], page_count) otherwise.
        """
        _UNIT_MARKER_RE = re.compile(r'UNIT\s*[-–]?\s*([IVX]+|\d+)', re.IGNORECASE)
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

                    # Title hint from text after the marker
                    after = text[m.end():].strip()
                    title_line = after.split('\n')[0].strip()[:80]
                    title_line = re.sub(r'^[-–:\s]+', '', title_line).strip()
                    unit_pages.append((unit_num, pg_idx, title_line))

                # Deduplicate: first occurrence of each unit number
                seen = set()
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
                    + ", ".join(f"Unit {u} @ page {p}" for u, p, _ in sorted_entries)
                )

                # Compute median font size for code-block detection
                all_sizes = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])
                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2] if all_sizes else 10.0
                header_threshold = median_size * 1.4

                blocks = []

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
    # Shared: Extract text from a page range
    # ------------------------------------------------------------------

    def _extract_page_range_text(
        self, pdf, start_page: int, end_page: int, header_threshold: float
    ) -> str:
        """Extract clean text from a range of PDF pages.

        Shared by Tier 1 (TOC), Tier 2 (UNIT markers).
        Applies: body crop, table annotation, word reconstruction,
        CODE/FIGURE markers, CID replacement.
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

            # Filter words outside tables
            filtered_words = [
                w for w in raw_words
                if not any(
                    w['x0'] >= tx0 and w['x1'] <= tx1
                    and w['top'] >= ttop and w['bottom'] <= tbot
                    for (tx0, ttop, tx1, tbot) in table_bboxes
                )
            ]

            lines = self._words_to_lines(filtered_words, header_threshold)

            page_line_texts = []
            in_code_block = False

            for ann_text in table_annotations:
                page_line_texts.append(ann_text)

            for line in lines:
                text = line['text'].strip()
                if not text or re.fullmatch(r'\d+', text):
                    continue

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
    # Tier 3: Font-heuristic (with FIXED bold threshold)
    # ------------------------------------------------------------------

    def _extract_blocks_with_headers(self) -> Tuple[List[Tuple[Optional[str], str]], int]:
        """Font-heuristic segmentation with FIXED bold detection.

        Key fix: Bold only triggers header when text size > 1.05× median,
        preventing 'Output:', 'Example:', etc. from creating false splits.
        """
        blocks = []
        current_heading: Optional[str] = None
        current_text_lines: List[str] = []
        page_count = 0

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page_count = len(pdf.pages)

                all_sizes = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])

                if not all_sizes:
                    logger.warning(f"No native text in {self.pdf_path.name}.")
                    median_size = 10.0
                    header_threshold = median_size * 1.4
                else:
                    all_sizes.sort()
                    median_size = all_sizes[len(all_sizes) // 2]
                    header_threshold = median_size * 1.4
                    logger.info(f"Body median: {median_size:.1f}pt, header threshold: {header_threshold:.1f}pt")

                for page in pdf.pages:
                    W = float(page.width)
                    H = float(page.height)
                    body = page.within_bbox((0, H * 0.10, W, H * 0.92))

                    raw_words_check = body.extract_words(extra_attrs=["size", "fontname"])
                    if not raw_words_check:
                        if _check_ocr_available():
                            ocr_text = _ocr_page(self.pdf_path, page.page_number - 1)
                            if ocr_text:
                                clean_lines = [
                                    ln for ln in ocr_text.splitlines()
                                    if ln.strip() and not re.fullmatch(r'\d+', ln.strip())
                                ]
                                current_text_lines.extend(clean_lines)
                        continue

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
                                        " | ".join(str(cell).strip() if cell is not None else '' for cell in row)
                                        for row in extracted if any(cell is not None for cell in row)
                                    )
                                    if table_text.strip():
                                        annotation = f"[TABLE:\n{table_text}\n]"
                                        if len(annotation) > 4000:
                                            annotation = annotation[:4000] + "\n...]"
                                    else:
                                        annotation = "[TABLE]"
                                else:
                                    annotation = "[TABLE]"
                            except Exception:
                                annotation = "[TABLE]"
                            table_annotations.append((bbox[1], annotation))
                    except Exception:
                        pass

                    raw_words = body.extract_words(extra_attrs=["size", "fontname"])
                    filtered_words = [
                        w for w in raw_words
                        if not any(
                            w['x0'] >= tx0 and w['x1'] <= tx1
                            and w['top'] >= ttop and w['bottom'] <= tbot
                            for (tx0, ttop, tx1, tbot) in table_bboxes
                        )
                    ]

                    lines = self._words_to_lines(filtered_words, header_threshold)

                    line_entries = []
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

                    line_iter = iter(lines)
                    in_code_block = False

                    for (y, entry_type, data) in line_entries:
                        if entry_type == 'table':
                            if in_code_block:
                                current_text_lines.append("[/CODE]")
                                in_code_block = False
                            current_text_lines.append(data)
                            continue

                        try:
                            line = next(line_iter)
                        except StopIteration:
                            break

                        text = line['text'].strip()
                        if not text:
                            continue

                        max_size = line['max_size']
                        is_code = line['is_code']

                        if re.fullmatch(r'\d+', text):
                            continue

                        fig_caption_match = re.match(
                            r'(Fig\.?\s*\d+\.\d+)\s+(.+)', text, re.IGNORECASE
                        )
                        if fig_caption_match and not is_code and max_size < header_threshold:
                            current_text_lines.append(
                                f"[FIGURE {fig_caption_match.group(1)}: {fig_caption_match.group(2).strip()}]"
                            )
                            continue

                        if is_code and not in_code_block:
                            current_text_lines.append("[CODE]")
                            in_code_block = True
                        elif not is_code and in_code_block:
                            current_text_lines.append("[/CODE]")
                            in_code_block = False

                        is_bold = line.get('is_bold', False)

                        # ══════════════════════════════════════════════════════
                        # FIX: Bold threshold — bold only triggers header when
                        # text is measurably larger than body text (>5% bigger).
                        # Before: is_header = (len(text) < 80 and (max_size >= header_threshold or is_bold))
                        # After:
                        is_header = (
                            len(text) < 80
                            and (
                                max_size >= header_threshold
                                or (is_bold and max_size > median_size * 1.05)
                            )
                        )
                        # ══════════════════════════════════════════════════════

                        if is_header:
                            if in_code_block:
                                current_text_lines.append("[/CODE]")
                                in_code_block = False
                            if current_text_lines:
                                blocks.append((current_heading, "\n".join(current_text_lines)))
                                current_text_lines = []
                            current_heading = text
                        else:
                            current_text_lines.append(text)

                    if in_code_block:
                        current_text_lines.append("[/CODE]")
                        in_code_block = False

            if current_text_lines:
                blocks.append((current_heading, "\n".join(current_text_lines)))

        except Exception as e:
            logger.error(f"Error reading PDF {self.pdf_path}: {e}")

        return blocks, page_count

    # ------------------------------------------------------------------
    # Segment type classification
    # ------------------------------------------------------------------

    def _classify_segment(self, heading: Optional[str], text: str) -> str:
        heading_l = (heading or "").lower().strip()
        if heading_l in _KNOWN_RUNNING_HEADERS:
            heading = None
            heading_l = ""

        for pat in _REFERENCE_TABLE_PATTERNS:
            if pat.search(heading_l):
                return "reference_table"

        for pat in _SOLUTION_PATTERNS:
            if pat.match(heading_l) or pat.match(text.lstrip()[:60]):
                return "solution"

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        exercise_line_count = sum(
            1 for line in lines
            if any(pat.match(line) for pat in _EXERCISE_PATTERNS)
        )
        heading_is_exercise = any(pat.match(heading_l) for pat in _EXERCISE_PATTERNS)
        if heading_is_exercise or (exercise_line_count >= 3 and exercise_line_count >= len(lines) * 0.4):
            return "exercise"

        for pat in _GLOSSARY_HEADING_PATTERNS:
            if pat.match(heading_l):
                return "glossary"
        if re.search(r'(^|\n)key\s+terms(\n|$)', text[:200], re.IGNORECASE):
            return "glossary"

        for pat in _SUMMARY_HEADING_PATTERNS:
            if pat.match(heading_l):
                return "summary"

        option_lines = sum(1 for ln in lines if _ASSESSMENT_OPTION_RE.match(ln))
        question_lines = sum(1 for ln in lines if _NUMBERED_QUESTION_RE.match(ln))
        if option_lines >= 3 and question_lines >= 2:
            return "assessment"

        for pat in _FRONTMATTER_PATTERNS:
            if pat.search(heading_l):
                return "frontmatter"

        # Check heading for frontmatter keyword
        if heading_l == "frontmatter":
            return "frontmatter"

        first_line = text.lstrip().split('\n')[0].strip().lower()
        for pat in _FRONTMATTER_PATTERNS:
            if pat.fullmatch(first_line):
                return "frontmatter"

        if _is_copyright_page(text):
            return "frontmatter"

        # NEW: Institutional boilerplate detection (critic_v3 Issue 4)
        if _is_institutional_boilerplate(text):
            return "frontmatter"

        return "instructional"

    # ------------------------------------------------------------------
    # Line reconstruction
    # ------------------------------------------------------------------

    def _words_to_lines(self, words: list, header_threshold: float) -> list:
        if not words:
            return []
        line_groups: dict = {}
        for w in words:
            key = round(w['doctop'] / 2) * 2
            line_groups.setdefault(key, []).append(w)

        result = []
        for y in sorted(line_groups):
            line_words = sorted(line_groups[y], key=lambda w: w['x0'])
            line_words = [
                w for w in line_words
                if not (re.fullmatch(r'\d+', w['text']) and w.get('size', 0) >= header_threshold)
            ]
            if not line_words:
                continue
            text = ' '.join(w['text'] for w in line_words)
            text = _CID_RE.sub('[?]', text)
            max_size = max(w.get('size', 0) for w in line_words)
            is_code = any(
                'Courier' in str(w.get('fontname', '')) or
                'Mono' in str(w.get('fontname', '')) or
                'Code' in str(w.get('fontname', ''))
                for w in line_words
            )
            is_bold = any(
                'Bold' in str(w.get('fontname', '')) or
                'Black' in str(w.get('fontname', '')) or
                'Heavy' in str(w.get('fontname', ''))
                for w in line_words
            )
            result.append({'text': text, 'max_size': max_size, 'is_code': is_code, 'is_bold': is_bold})
        return result

    # ------------------------------------------------------------------
    # Merge and chunk
    # ------------------------------------------------------------------

    def _merge_short_blocks(self, blocks: List[Tuple[Optional[str], str]]) -> List[Tuple[Optional[str], str]]:
        blocks = list(blocks)
        while len(blocks) > 1:
            mergeable = []
            for i in range(len(blocks) - 1):
                h0, t0 = blocks[i]
                h1, t1 = blocks[i + 1]
                if len(t0) + len(t1) < self.max_chars:
                    if h1 and re.match(r'^(chapter|module|unit|part)\s+\d+', h1.strip(), re.IGNORECASE):
                        continue
                    mergeable.append(i)
            if not mergeable:
                break
            best_i = min(mergeable, key=lambda i: len(blocks[i][1]) + len(blocks[i+1][1]))
            h0, t0 = blocks[best_i]
            h1, t1 = blocks[best_i + 1]
            blocks[best_i] = (h0 if h0 else h1, t0 + "\n\n" + t1)
            blocks.pop(best_i + 1)
        return blocks

    def _chunk_text(self, text: str) -> List[str]:
        if len(text) <= self.max_chars:
            return [text]
        chunks = []
        paragraphs = re.split(r'\n\n+', text)
        current_chunk = ""
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if len(p) > self.max_chars:
                sentences = re.split(r'(?<=[.!?])\s+', p)
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
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