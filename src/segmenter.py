import logging
from pathlib import Path
from typing import List, Tuple, Optional
import pdfplumber
import re

from src.models import Segment

logger = logging.getLogger(__name__)

class SmartSegmenter:
    """Deterministically extracts and segments text from PDFs using font heuristics.

    Targets chapter-level granularity: a ~40-page PDF should produce ~4-6 segments,
    not one per bold subheading. Header detection requires a significant font-size jump
    (>= 1.4x body median) so only true chapter/section titles trigger a new segment.
    Short blocks are merged into the following block to prevent micro-segments.
    """

    def __init__(self, pdf_path: Path, max_chars: int = 8000, min_chars: int = 600):
        self.pdf_path = Path(pdf_path)
        self.max_chars = max_chars
        self.min_chars = min_chars

    def segment(self) -> List[Segment]:
        """Extract text from PDF, group by major headers, and apply fallback chunking."""
        logger.info(f"Segmenting PDF: {self.pdf_path.name}")
        raw_blocks = self._extract_blocks_with_headers()
        merged_blocks = self._merge_short_blocks(raw_blocks)

        segments = []
        segment_id = 1

        for heading, text in merged_blocks:
            # Fallback chunking if text block is too long
            chunks = self._chunk_text(text)
            for chunk in chunks:
                if not chunk.strip():
                    continue
                segments.append(Segment(
                    segment_id=segment_id,
                    heading=heading,
                    text=chunk.strip()
                ))
                segment_id += 1

        logger.info(f"Generated {len(segments)} segments for {self.pdf_path.name}")
        return segments

    def _extract_blocks_with_headers(self) -> List[Tuple[Optional[str], str]]:
        """Extract text from PDF and identify major section headers by significant font-size jump.

        Only triggers a new block when the line's max font size is >= 1.4x the body median
        AND the line is short enough to be a title (< 80 chars). Bold-only formatting is
        intentionally ignored to avoid treating inline emphasis as section breaks.
        """
        blocks = []
        current_heading: Optional[str] = None
        current_text_lines: List[str] = []

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                # 1. Determine median font size across ALL pages for a stable baseline
                all_sizes = []
                for page in pdf.pages:
                    words = page.extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])

                if not all_sizes:
                    logger.warning(f"Could not detect text natively in {self.pdf_path.name}. Possibly scanned.")
                    return [(None, "")]

                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2]
                header_threshold = median_size * 1.4
                logger.info(f"Body median font size: {median_size:.1f}pt — chapter header threshold: {header_threshold:.1f}pt")

                # 2. Extract and group lines
                for page in pdf.pages:
                    lines = page.extract_text_lines(return_chars=True)
                    for line in lines:
                        if not line.get('chars'):
                            continue

                        chars = line['chars']
                        max_size = max([c.get('size', 0) for c in chars])

                        text = line.get('text', '').strip()
                        if not text:
                            continue

                        # Skip pure page numbers (digits only, possibly with whitespace)
                        if re.fullmatch(r'\d+', text):
                            continue

                        # Chapter-level header: significant size jump AND short enough to be a title
                        is_header = (len(text) < 80 and max_size >= header_threshold)

                        if is_header:
                            # Save previous block before starting a new section
                            if current_text_lines:
                                blocks.append((current_heading, "\n".join(current_text_lines)))
                                current_text_lines = []
                            current_heading = text
                        else:
                            current_text_lines.append(text)

            # Append the final block
            if current_text_lines:
                blocks.append((current_heading, "\n".join(current_text_lines)))

        except Exception as e:
            logger.error(f"Error reading PDF {self.pdf_path}: {e}")
            
        return blocks

    def _merge_short_blocks(self, blocks: List[Tuple[Optional[str], str]]) -> List[Tuple[Optional[str], str]]:
        """Merge consecutive blocks that are too short to stand alone as evaluation segments.

        Accumulates blocks into a buffer until the buffer reaches min_chars. The heading
        of the first block in a merged run is preserved. Any trailing short content is
        appended to the last emitted segment rather than discarded.
        """
        if not blocks:
            return []

        merged: List[Tuple[Optional[str], str]] = []
        buffer_heading: Optional[str] = None
        buffer_text = ""

        for heading, text in blocks:
            if not buffer_text:
                buffer_heading = heading
                buffer_text = text
            else:
                buffer_text = buffer_text + "\n\n" + text

            if len(buffer_text) >= self.min_chars:
                merged.append((buffer_heading, buffer_text))
                buffer_heading = None
                buffer_text = ""

        # Flush remaining content: append to the last segment or emit as its own
        if buffer_text:
            if merged:
                last_h, last_t = merged[-1]
                merged[-1] = (last_h, last_t + "\n\n" + buffer_text)
            else:
                merged.append((buffer_heading, buffer_text))

        logger.debug(f"Block merge: {len(blocks)} raw blocks → {len(merged)} merged blocks")
        return merged

    def _chunk_text(self, text: str) -> List[str]:
        """Split text that exceeds max_chars safely by sentence boundaries."""
        if len(text) <= self.max_chars:
            return [text]
            
        chunks = []
        # Split by obvious sentence endings or double newlines (paragraphs)
        # We'll use a regex to split on sentence boundaries keeping the punctuation
        sentences = re.split(r'(?<=[.!?])\s+|\n\n', text)
        
        current_chunk = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence: continue
            
            # If a single sentence is bizarrely long, we'll just have to hard-split it
            if len(sentence) > self.max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # Hard chunk the massive sentence
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
