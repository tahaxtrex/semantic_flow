import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional
import pdfplumber
import re

from src.models import Segment

logger = logging.getLogger(__name__)

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
        """Extract text from PDF, group by major headers, and apply fallback chunking."""
        logger.info(f"Segmenting PDF: {self.pdf_path.name}")
        raw_blocks, page_count = self._extract_blocks_with_headers()
        max_segments = max(1, page_count // 10)
        logger.info(f"PDF has {page_count} pages — targeting ≤{max_segments} segments")
        merged_blocks = self._merge_to_target(raw_blocks, max_segments)

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
    # Segment type classification (critic.md Issue 8)
    # ------------------------------------------------------------------

    def _classify_segment(self, heading: Optional[str], text: str) -> str:
        """Classify a segment as instructional, exercise, solution, or reference_table."""
        heading_l = (heading or "").lower()

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
            result.append({'text': text, 'max_size': max_size, 'is_code': is_code})
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
                    logger.warning(f"Could not detect text natively in {self.pdf_path.name}. Possibly scanned.")
                    return [(None, "")], page_count

                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2]
                header_threshold = median_size * 1.4
                logger.info(f"Body median font size: {median_size:.1f}pt — chapter header threshold: {header_threshold:.1f}pt")

                # 2. Extract and group lines per page
                for page in pdf.pages:
                    W = float(page.width)
                    H = float(page.height)

                    # Crop to body region — strips running headers (top 10%) and footers (bottom 8%)
                    body = page.within_bbox((0, H * 0.10, W, H * 0.92))

                    # Detect tables in the body region; collect bounding boxes
                    table_bboxes = []
                    table_annotations = []  # list of (y_pos, annotation_text)
                    try:
                        tables = body.find_tables()
                        for table in tables:
                            bbox = table.bbox  # (x0, top, x1, bottom)
                            table_bboxes.append(bbox)
                            # Build annotation from first row of extracted data
                            try:
                                extracted = table.extract()
                                if extracted and extracted[0]:
                                    first_row = [str(cell) if cell is not None else '' for cell in extracted[0]]
                                    annotation = "[TABLE: " + " | ".join(first_row) + "]"
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

                        # Chapter-level header: significant size jump AND short enough to be a title
                        is_header = (len(text) < 80 and max_size >= header_threshold)

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

    def _merge_to_target(
        self,
        blocks: List[Tuple[Optional[str], str]],
        max_segments: int
    ) -> List[Tuple[Optional[str], str]]:
        """Greedily merge adjacent block pairs until len(blocks) <= max_segments.

        At each step merges the pair whose combined text is shortest (least information
        loss heuristic). The heading of the first block in each merged pair is kept.
        """
        blocks = list(blocks)  # copy
        while len(blocks) > max_segments:
            # Find adjacent pair with smallest combined text length
            best_i = min(range(len(blocks) - 1),
                         key=lambda i: len(blocks[i][1]) + len(blocks[i+1][1]))
            h0, t0 = blocks[best_i]
            h1, t1 = blocks[best_i + 1]
            blocks[best_i] = (h0, t0 + "\n\n" + t1)
            blocks.pop(best_i + 1)
        logger.debug(f"Block merge: targeted {max_segments} segments → {len(blocks)} blocks")
        return blocks

    def _chunk_text(self, text: str) -> List[str]:
        """Split text that exceeds max_chars safely by sentence boundaries."""
        if len(text) <= self.max_chars:
            return [text]

        chunks = []
        sentences = re.split(r'(?<=[.!?])\s+|\n\n', text)

        current_chunk = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence: continue

            # If a single sentence is bizarrely long, hard-split it
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
            chunks.append(current_chunk.strip())

        return chunks
