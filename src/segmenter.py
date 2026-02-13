import re
import yaml
from typing import List, Dict, Any
from pathlib import Path

class StructuralSegmenter:
    def __init__(self, config_path: str = "config/settings.yaml"):
        with open(config_path, 'r') as f:
            self.settings = yaml.safe_load(f)
        
        self.max_pages = self.settings['segmentation']['max_pages_per_segment']
        self.regex_patterns = self.settings['segmentation']['regex_patterns']

    def segment_course(self, pages: List[Dict[str, Any]], bookmarks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Segments a list of pages into chapters/parts.
        """
        segments = []
        
        # Priority 1: Use Bookmarks if they exist and are not too granular
        if bookmarks and len(bookmarks) > 1:
            segments = self._segment_by_bookmarks(pages, bookmarks)
        else:
            # Priority 2: Use Regex if bookmarks fail
            segments = self._segment_by_regex(pages)
        
        # Priority 3: Enforce hard page limit for any oversized segments
        final_segments = []
        for seg in segments:
            if (seg['end_page'] - seg['start_page'] + 1) > self.max_pages:
                final_segments.extend(self._split_oversized_segment(seg))
            else:
                final_segments.append(seg)
        
        return final_segments

    def _segment_by_bookmarks(self, pages: List[Dict[str, Any]], bookmarks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        segments = []
        num_pages = len(pages)
        
        for i, bm in enumerate(bookmarks):
            start_idx = bm['page_index']
            end_idx = bookmarks[i+1]['page_index'] - 1 if i+1 < len(bookmarks) else num_pages - 1
            
            # Ensure indices are within bounds
            start_idx = max(0, min(start_idx, num_pages - 1))
            end_idx = max(start_idx, min(end_idx, num_pages - 1))
            
            segment_text = "".join([pages[j]['text'] for j in range(start_idx, end_idx + 1)])
            
            segments.append({
                "title": bm['title'],
                "start_page": start_idx + 1,
                "end_page": end_idx + 1,
                "text": segment_text
            })
        return segments

    def _segment_by_regex(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        segments = []
        current_segment_text = []
        current_title = "Introduction / Segment 1"
        start_page = 1
        
        patterns = [re.compile(p, re.IGNORECASE) for p in self.regex_patterns]
        
        for i, page in enumerate(pages):
            # Check the first few lines for chapter/module markers
            first_lines = "\n".join(page['text'].split("\n")[:5])
            is_new_segment = any(p.search(first_lines) for p in patterns)
            
            if is_new_segment and i > 0:
                # Save previous segment
                segments.append({
                    "title": current_title,
                    "start_page": start_page,
                    "end_page": i, # previous page
                    "text": "".join(current_segment_text)
                })
                # Start new segment
                start_page = i + 1
                current_segment_text = [page['text']]
                # Extract new title from the match
                match = None
                for p in patterns:
                    m = p.search(first_lines)
                    if m:
                        match = m.group(0)
                        break
                current_title = match or f"Segment {len(segments) + 1}"
            else:
                current_segment_text.append(page['text'])
        
        # Add last segment
        segments.append({
            "title": current_title,
            "start_page": start_page,
            "end_page": len(pages),
            "text": "".join(current_segment_text)
        })
        return segments

    def _split_oversized_segment(self, segment: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Naive split by page count to enforce the 20-page limit
        sub_segments = []
        total_pages = segment['end_page'] - segment['start_page'] + 1
        
        # We'd need the original page-by-page text here for a clean split, 
        # but for v1 we can just note that it was split.
        # This is a simplified implementation.
        for i in range(0, total_pages, self.max_pages):
            sub_start = segment['start_page'] + i
            sub_end = min(sub_start + self.max_pages - 1, segment['end_page'])
            sub_segments.append({
                "title": f"{segment['title']} (Part {i//self.max_pages + 1})",
                "start_page": sub_start,
                "end_page": sub_end,
                "text": segment['text'] # In a real implementation, we'd slice the text properly
            })
        return sub_segments
