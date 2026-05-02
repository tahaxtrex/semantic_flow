"""Prompt sanitizer for KAM findings.

Strips control characters and XML-like tags from extracted concepts and evidence
to prevent prompt injection routing through the extraction engine, per spec §37.1.
Truncates excessively long fields.
"""

import re

# Strip XML-like tags that might confuse the evaluator prompt's boundaries
_TAG_RE = re.compile(r"</?(?:SOURCE_TEXT|KAM_GRAPH_CONTEXT|system|user|assistant)[^>]*>", re.IGNORECASE)

# Strip control characters (0x00-0x1F) except tab (0x09) and newline (0x0A, 0x0D)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

MAX_FIELD_LENGTH = 500


def sanitize_text(text: str, max_length: int = MAX_FIELD_LENGTH) -> str:
    """Sanitizes text extracted from the document to make it safe for LLM prompts."""
    if not text:
        return ""
    
    # 1. Remove dangerous XML/Prompt tags
    text = _TAG_RE.sub("", text)
    
    # 2. Remove control characters
    text = _CTRL_RE.sub("", text)
    
    # 3. Truncate to prevent context bloat
    if len(text) > max_length:
        text = text[:max_length] + "..."
        
    return text.strip()
