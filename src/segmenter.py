import logging
from pathlib import Path
from typing import List, Tuple, Optional
import pdfplumber
import re

from src.models import Segment

logger = logging.getLogger(__name__)

class SmartSegmenter:
    """Deterministically extracts and segments text from PDFs using font heuristics."""
    
    def __init__(self, pdf_path: Path, max_chars: int = 3500):
        self.pdf_path = Path(pdf_path)
        self.max_chars = max_chars

    def segment(self) -> List[Segment]:
        """Extract text from PDF, group by headers, and apply fallback chunking."""
        logger.info(f"Segmenting PDF: {self.pdf_path.name}")
        raw_blocks = self._extract_blocks_with_headers()
        
        segments = []
        segment_id = 1
        
        for heading, text in raw_blocks:
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
        """Extract text from PDF and identify headers based on font size/boldness."""
        blocks = []
        current_heading: Optional[str] = None
        current_text_lines: List[str] = []
        
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                # 1. Determine median font size across a sample of pages
                sample_pages = min(len(pdf.pages), 10)
                all_sizes = []
                for i in range(sample_pages):
                    words = pdf.pages[i].extract_words(extra_attrs=["size"])
                    all_sizes.extend([w['size'] for w in words if 'size' in w])
                
                if not all_sizes:
                    logger.warning(f"Could not detect text natively in {self.pdf_path.name}. Possibly scanned.")
                    return [(None, "")]
                    
                all_sizes.sort()
                median_size = all_sizes[len(all_sizes) // 2]
                logger.debug(f"Calculated median font size: {median_size}")
                
                # 2. Extract and group lines
                for page in pdf.pages:
                    lines = page.extract_text_lines(return_chars=True)
                    for line in lines:
                        if not line.get('chars'):
                            continue
                            
                        chars = line['chars']
                        max_size = max([c.get('size', 0) for c in chars])
                        is_bold = any('Bold' in str(c.get('fontname', '')) for c in chars)
                        
                        text = line.get('text', '').strip()
                        if not text:
                            continue
                            
                        # Heuristic: If size is > 1.5 pts larger than median, OR bold and relatively short
                        is_header = False
                        if len(text) < 150: # Headers are usually not full long paragraphs
                            if max_size > (median_size + 1.5) or is_bold:
                                is_header = True
                                
                        if is_header:
                            # Save previous block
                            if current_text_lines:
                                blocks.append((current_heading, "\n".join(current_text_lines)))
                                current_text_lines = []
                            current_heading = text
                        else:
                            current_text_lines.append(text)
                            
            # Append final block
            if current_text_lines:
                blocks.append((current_heading, "\n".join(current_text_lines)))
                
        except Exception as e:
            logger.error(f"Error reading PDF {self.pdf_path}: {e}")
            
        return blocks

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
