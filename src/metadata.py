import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pdfplumber
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Expanded patterns ─────────────────────────────────────────────────────────

_TITLE_PATTERNS = [
    # "Title: X" style
    re.compile(r'^title\s*[:\-]\s*(.{5,120})', re.IGNORECASE | re.MULTILINE),
    # "About <Title>" preface header
    re.compile(r'About\s+([A-Z][^.\n]{10,100})\n', re.MULTILINE),
]

_AUTHOR_PATTERNS = [
    # ALL-CAPS Senior Contributing Author line (OpenStax pattern — name comes after label)
    re.compile(r'(?:senior\s+)?contributing\s+author[s]?\s*\n\s*(?:DR\.?|PROF\.?)?\s*([A-Z]{2,}(?:\s+[A-Z\.]{1,5})*(?:\s+[A-Z]{2,})+)', re.IGNORECASE),
    # "by Author Name" on its own line (cover pages)
    re.compile(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z]\.?\s*)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', re.MULTILINE),
    # "Author: Name" or "Written by: Name"
    re.compile(r'(?:author|written by|prepared by)\s*[:\-]\s*([A-Z][^.\n]{5,80})', re.IGNORECASE),
    # "Senior Contributing Author\nDr. Name Name" (title-case OpenStax pattern)
    re.compile(r'(?:senior\s+)?contributing\s+author[s]?\s*\n\s*((?:Dr\.|Prof\.)?\s*[A-Z][^.\n]{5,60})', re.IGNORECASE),
    # Direct "Dr. / Prof. Name" after authorship label on same line
    re.compile(r'(?:author|by)[:\s]+(?:Dr\.|Prof\.)?\s*([A-Z][a-z]+(?:\s+[A-Z]\.?\s*[A-Z][a-z]+)+)', re.IGNORECASE),
]

_AUDIENCE_PATTERNS = [
    re.compile(r'(?:intended for|suitable for|designed for|aimed at|for students|'
               r'for\s+\w+\s+(?:students|developers|programmers)|'
               r'appeals to|written for|targeted(?:\s+at)?)\s+([^.]{10,150})', re.IGNORECASE),
    # "This (book|text|course) is (an|a) [adjective] [noun] for [audience]"
    re.compile(r'this\s+(?:book|text|textbook|course|resource)\s+is\s+(?:an?\s+)?'
               r'[\w\s]{0,30}(?:for|to)\s+([^.]{10,120})', re.IGNORECASE),
    # "introductory / undergraduate / graduate" level signals
    re.compile(r'\b(introductory|undergraduate|graduate|advanced|beginner|intermediate)\s+'
               r'(?:course|text|students?|level)[^.]{0,80}', re.IGNORECASE),
]

_PREREQ_PATTERNS = [
    re.compile(r'(?:prerequisite[s]?|prior knowledge|prior experience|'
               r'assumes|assumes knowledge|requires|background in|'
               r'familiarity with|should (?:know|have|be familiar))\s*[:\-]?\s*([^.]{10,150})',
               re.IGNORECASE),
    # "Students who have completed X" 
    re.compile(r'students?\s+who\s+(?:have|has)\s+(?:completed|taken|studied)\s+([^.]{5,100})',
               re.IGNORECASE),
    # "no prerequisites" — explicitly capture that too
    re.compile(r'(no\s+(?:formal\s+)?prerequisite[s]?[^.]{0,60})', re.IGNORECASE),
]

_OUTCOME_PATTERNS = [
    re.compile(r'(?:you will (?:learn|be able to)|by the end|after completing|'
               r'learning objectives?|upon completion|objectives?\s*[:\-]|'
               r'students?\s+will\s+(?:be able to|learn|understand|'
               r'develop|gain|demonstrate))\s*[:\-]?\s*([^.]{10,250})',
               re.IGNORECASE),
]

_DESCRIPTION_PATTERNS = [
    # "About [Title]\n<description paragraph>"
    re.compile(r'About\s+[\w\s]+?\n\s*([A-Z][^.]{30,400}\.)', re.DOTALL),
    # "This (book|text|course) provides/covers/introduces..."
    re.compile(
        r'(?:this\s+(?:book|textbook|text|course|resource))\s+'
        r'(?:provides?|covers?|introduces?|is\s+designed\s+to|aims?\s+to)\s+'
        r'([^.]{30,350}\.)', re.IGNORECASE
    ),
    # First substantial sentence of a Preface / Introduction section
    re.compile(r'(?:Preface|Introduction)\s*\n+([A-Z][^\n]{50,400})', re.DOTALL),
    # Paragraph starting with the book's subject that looks like a blurb (min 60 chars, ends with period)
    re.compile(r'([A-Z][A-Za-z\s,;]{60,350}(?:course|textbook|students|concepts|topics|skills)[^.]{0,100}\.)', re.DOTALL),
]

_SUBJECT_PATTERNS = [
    # "course in [Subject]" / "course on [Subject]"
    re.compile(r'course\s+(?:in|on)\s+([A-Z][^.,]{3,60})', re.IGNORECASE),
    # "textbook for [Subject]"
    re.compile(r'textbook\s+(?:for|in|on)\s+([A-Z][^.,]{3,60})', re.IGNORECASE),
    # "Introduction to [Subject]" type titles
    re.compile(r'^(?:introduction\s+to|foundations?\s+(?:of|in)|'
               r'fundamentals?\s+of)\s+([A-Z][^.\n]{3,80})',
               re.IGNORECASE | re.MULTILINE),
]

_ISBN_PATTERN = re.compile(
    r'ISBN(?:-1[03])?\s*[:\-]?\s*((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])', re.IGNORECASE
)
_YEAR_PATTERN = re.compile(r'\b(20[0-2]\d|19[89]\d)\b')
_PUBLISHER_PATTERNS = [
    re.compile(r'[Pp]ublished\s+by\s+([^.\n]{5,80})'),
    re.compile(r'[Cc]opyright\s+(?:\d{4}\s+)?([A-Z][^.\n]{5,60}(?:Press|Publishing|University|Inc\.|LLC|College))'),
    re.compile(r'©\s*\d{4}\s+([A-Z][^.\n]{5,60}(?:Press|Publishing|University|Inc\.|LLC|College))'),
    # Generic known educational publishers
    re.compile(r'\b(OpenStax(?:\s+College)?)\b', re.IGNORECASE),
    re.compile(r'^([A-Z][^.\n]{3,50}(?:Press|Publishing|Books|Education))', re.MULTILINE),
]


class CourseMetadata(BaseModel):
    title: str = "Unknown"
    author: str = "Unknown"
    target_audience: str = "Unknown"
    subject: str = "Unknown"
    source: str = "Unknown"
    description: str = "Unknown"
    prerequisites: List[str] = Field(default_factory=list)
    learning_outcomes: List[str] = Field(default_factory=list)
    # New fields
    publisher: str = "Unknown"
    year: str = "Unknown"
    isbn: str = "Unknown"
    level: str = "Unknown"          # introductory / intermediate / advanced
    contributing_authors: List[str] = Field(default_factory=list)


# ── AI Metadata Extractor ─────────────────────────────────────────────────────

_AI_SYSTEM_PROMPT = """
You are an expert librarian and course cataloguer. You will be given raw text extracted
from the first pages of a course textbook/PDF. Extract metadata as a single JSON object.

Rules:
- Return ONLY valid JSON, no markdown fences, no commentary.
- If a field cannot be determined from the text, use null.
- prerequisites and learning_outcomes must be JSON arrays of strings (can be empty).
- contributing_authors must be a JSON array of strings (can be empty).
- level must be one of: "Introductory", "Intermediate", "Advanced", or null.
- year should be a 4-digit string (e.g. "2024") or null.
- isbn should be the raw ISBN string or null.

Required JSON schema:
{
  "title": string | null,
  "author": string | null,
  "target_audience": string | null,
  "subject": string | null,
  "description": string | null,
  "prerequisites": [string],
  "learning_outcomes": [string],
  "publisher": string | null,
  "year": string | null,
  "isbn": string | null,
  "level": string | null,
  "contributing_authors": [string]
}
""".strip()


class AIMetadataExtractor:
    """
    Extracts CourseMetadata fields using an LLM.
    Tries Claude first; falls back to Gemini if Claude fails.
    """

    CLAUDE_MODEL  = "claude-sonnet-4-6"
    GEMINI_MODEL  = "gemini-2.5-flash"
    MAX_RETRIES   = 2
    RETRY_BACKOFF = 3  # seconds, doubles each retry

    def __init__(
        self,
        anthropic_key: Optional[str] = None,
        gemini_key: Optional[str] = None,
        preferred_model: str = "claude",
    ):
        self.preferred_model = preferred_model.lower()
        self._anthropic_client = None
        self._gemini_client    = None

        if anthropic_key:
            try:
                from anthropic import Anthropic
                self._anthropic_client = Anthropic(api_key=anthropic_key)
            except ImportError:
                logger.warning("anthropic package not installed — Claude unavailable.")

        if gemini_key:
            try:
                from google import genai
                self._gemini_client = genai.Client(api_key=gemini_key)
            except ImportError:
                logger.warning("google-genai package not installed — Gemini unavailable.")

    # ── Public entry point ────────────────────────────────────────────────

    def extract(self, text: str, source: str) -> "CourseMetadata":
        """
        Given raw front-matter text and a source label, return a CourseMetadata
        populated by the LLM.  Fields the LLM cannot determine are left as
        'Unknown' (scalars) or [] (lists).
        """
        user_prompt = (
            f"Extract metadata from the following course text. Source: {source}\n\n"
            f"{text[:12000]}"  # stay well within context limits
        )

        response_text = None

        if self.preferred_model == "claude":
            response_text = self._try_claude(user_prompt)
            if response_text is None:
                logger.warning("Claude failed — falling back to Gemini.")
                response_text = self._try_gemini(user_prompt)
        else:
            response_text = self._try_gemini(user_prompt)
            if response_text is None:
                logger.warning("Gemini failed — falling back to Claude.")
                response_text = self._try_claude(user_prompt)

        if response_text is None:
            logger.error("All AI providers failed for metadata extraction.")
            return CourseMetadata(source=source)

        return self._parse_response(response_text, source)

    # ── LLM callers ───────────────────────────────────────────────────────

    def _try_claude(self, user_prompt: str) -> Optional[str]:
        if self._anthropic_client is None:
            return None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(f"AI metadata: calling Claude (attempt {attempt}).")
                resp = self._anthropic_client.messages.create(
                    model=self.CLAUDE_MODEL,
                    max_tokens=1024,
                    temperature=0.0,
                    system=_AI_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return resp.content[0].text
            except Exception as e:
                wait = self.RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(f"Claude attempt {attempt} failed: {e}. Waiting {wait}s.")
                if attempt < self.MAX_RETRIES:
                    time.sleep(wait)
        return None

    def _try_gemini(self, user_prompt: str) -> Optional[str]:
        if self._gemini_client is None:
            return None
        try:
            from google.genai import types as genai_types
        except ImportError:
            return None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(f"AI metadata: calling Gemini (attempt {attempt}).")
                resp = self._gemini_client.models.generate_content(
                    model=self.GEMINI_MODEL,
                    contents=_AI_SYSTEM_PROMPT + "\n\n" + user_prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json",
                    ),
                )
                return resp.text
            except Exception as e:
                wait = self.RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(f"Gemini attempt {attempt} failed: {e}. Waiting {wait}s.")
                if attempt < self.MAX_RETRIES:
                    time.sleep(wait)
        return None

    # ── Response parser ───────────────────────────────────────────────────

    def _parse_response(self, text: str, source: str) -> "CourseMetadata":
        # Strip markdown fences if the model included them
        clean = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"AI response was not valid JSON: {e}\nRaw: {text[:300]}")
            return CourseMetadata(source=source)

        # Coerce null → our sentinel defaults so Pydantic is happy
        for scalar_field in ("title", "author", "target_audience", "subject",
                             "description", "publisher", "year", "isbn", "level"):
            if data.get(scalar_field) is None:
                data[scalar_field] = "Unknown"
        for list_field in ("prerequisites", "learning_outcomes", "contributing_authors"):
            if not isinstance(data.get(list_field), list):
                data[list_field] = []

        data["source"] = source

        try:
            return CourseMetadata(**{k: v for k, v in data.items()
                                     if k in CourseMetadata.model_fields})
        except Exception as e:
            logger.error(f"CourseMetadata validation failed: {e}")
            return CourseMetadata(source=source)


class MetadataIngestor:
    """
    Metadata extractor with deterministic regex pre-pass + optional AI fill.

    Extraction pipeline:
      1. PDF document properties (title, author, subject)
      2. Font-size heuristic title detection
      3. Pattern/regex inference from front-matter text
      4. (Optional) AI pass (Claude → Gemini fallback) fills remaining Unknown fields
    """

    def __init__(
        self,
        course_pdf_path: Path,
        metadata_source: Optional[str] = None,
        use_ai: bool = False,
        preferred_model: str = "claude",
        # Legacy parameters kept for backwards compatibility
        llm_fallback: bool = False,
        llm_caller=None,
    ):
        self.metadata_source  = str(metadata_source) if metadata_source else None
        self.course_pdf_path  = Path(course_pdf_path)
        self.base_name        = self.course_pdf_path.stem
        self.dir_path         = self.course_pdf_path.parent
        self.use_ai           = use_ai
        self.preferred_model  = preferred_model
        # Legacy LLM fallback (kept for backwards compat, lower priority than use_ai)
        self.llm_fallback     = llm_fallback
        self.llm_caller       = llm_caller

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────────────

    def ingest(self) -> CourseMetadata:
        if self.metadata_source:
            return self._from_explicit_source()
        return self._autoscan()

    # ─────────────────────────────────────────────────────────────────────────
    # Source routing (unchanged logic, extended for new fields)
    # ─────────────────────────────────────────────────────────────────────────

    def _from_explicit_source(self) -> CourseMetadata:
        src = self.metadata_source
        if src.startswith("http") or src.startswith("www."):
            return self._parse_url()
        path = Path(src)
        if not path.exists():
            logger.warning(f"Metadata file {path} not found.")
            return CourseMetadata(source=self.course_pdf_path.name)
        ext = path.suffix.lower()
        dispatch = {'.json': self._parse_json, '.txt': self._parse_txt, '.html': self._parse_html}
        return dispatch.get(ext, self._extract_metadata_from_pdf)(path)

    def _autoscan(self) -> CourseMetadata:
        for ext, parser in [('.json', self._parse_json), ('.txt', self._parse_txt), ('.html', self._parse_html)]:
            p = self.dir_path / f"{self.base_name}{ext}"
            if p.exists():
                logger.info(f"Found external metadata: {p}")
                return parser(p)
        logger.info(f"No external metadata for '{self.base_name}'. Falling back to PDF.")
        return self._extract_metadata_from_pdf(self.course_pdf_path)

    # ─────────────────────────────────────────────────────────────────────────
    # Core PDF extraction — heavily improved
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_metadata_from_pdf(self, pdf_path: Path) -> CourseMetadata:
        metadata = CourseMetadata(source=pdf_path.name)
        front_text = ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                doc_info = pdf.metadata or {}
                self._apply_pdf_properties(metadata, doc_info, pdf_path)

                # ── Gather text from cover + front matter ──────────────────
                cover_text = self._extract_cover_text(pdf)       # pages 0-2
                front_text = self._extract_front_matter(pdf)     # pages 0-14 / 6000 words

                # ── Title: try font-size heuristic on cover first ──────────
                if metadata.title == "Unknown" or metadata.title == pdf_path.stem:
                    title_by_font = self._extract_title_by_font(pdf)
                    if title_by_font:
                        metadata.title = title_by_font
                        logger.info(f"Font-heuristic title: {metadata.title}")

                # ── Fill every field from text (regex / heuristics) ──────
                self._infer_from_text(metadata, cover_text, front_text)

                # ── Legacy LLM fallback ───────────────────────────────
                if self.llm_fallback and self.llm_caller:
                    self._llm_fill(metadata, front_text[:3000])

        except Exception as e:
            logger.error(f"PDF extraction failed for {pdf_path}: {e}")

        # ── AI pass: fill any remaining Unknown / empty fields ──────────
        if self.use_ai:
            anthropic_key = os.getenv("ANTHROPIC_API_KEY")
            gemini_key    = os.getenv("GEMINI_API_KEY")
            if not anthropic_key and not gemini_key:
                logger.warning("use_ai=True but neither ANTHROPIC_API_KEY nor GEMINI_API_KEY is set. Skipping AI pass.")
            else:
                ai = AIMetadataExtractor(
                    anthropic_key=anthropic_key,
                    gemini_key=gemini_key,
                    preferred_model=self.preferred_model,
                )
                ai_result = ai.extract(front_text, pdf_path.name)
                metadata  = self._merge(metadata, ai_result)

        return metadata

    @staticmethod
    def _merge(base: CourseMetadata, ai: CourseMetadata) -> CourseMetadata:
        """
        Merge AI result into the base (deterministic) result.
        AI wins for any scalar field still 'Unknown' and any list still empty.
        """
        data = base.model_dump()
        ai_data = ai.model_dump()
        for field, val in data.items():
            ai_val = ai_data.get(field)
            if isinstance(val, list):
                if not val and ai_val:
                    data[field] = ai_val
            else:
                if val in ("Unknown", None, "") and ai_val not in ("Unknown", None, ""):
                    data[field] = ai_val
        return CourseMetadata(**data)

    # ─────────────────────────────────────────────────────────────────────────
    # Text extraction helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_cover_text(self, pdf) -> str:
        """First 3 pages — most likely to have title/author. Falls back to word-extraction for image-heavy pages."""
        parts = []
        for page in pdf.pages[:3]:
            text = page.extract_text() or ""
            if not text.strip():
                # Try word-based extraction as fallback for image-heavy PDFs
                try:
                    words = page.extract_words()
                    text = " ".join(w['text'] for w in words)
                except Exception:
                    text = ""
            parts.append(text)
        return "\n".join(parts)

    def _extract_front_matter(self, pdf, max_pages: int = 20, max_words: int = 6000) -> str:
        """
        Extended scan: up to 20 pages or 6000 words.
        Increased from original 15/5000 to catch prefaces that start later.
        """
        parts = []
        word_count = 0
        for page in pdf.pages[:max_pages]:
            H, W = float(page.height), float(page.width)
            body = page.within_bbox((0, H * 0.08, W, H * 0.93))
            words = body.extract_words()
            page_text = " ".join(w['text'] for w in words)
            parts.append(page_text)
            word_count += len(page_text.split())
            if word_count >= max_words:
                break
        return "\n".join(parts)

    def _extract_title_by_font(self, pdf) -> Optional[str]:
        """
        Find the largest-font text block on the first 3 pages.
        Works for OpenStax, Pearson, Springer, O'Reilly, etc.
        Returns None if no reliable candidate found.
        """
        best_size = 0
        best_text = None
        
        # Scan first 3 pages to find the maximum font size used for readable text
        for page in pdf.pages[:3]:
            try:
                words = page.extract_words(extra_attrs=["size"])
            except Exception:
                continue
            for w in words:
                size = float(w.get("size", 0))
                text = w.get("text", "").strip()
                # Loosen the length constraint to catch large short words like "A", "An" if they are the ONLY large words,
                # though usually titles are longer.
                if size > best_size and len(text) >= 2 and text.isascii():
                    best_size = size
                    best_text = text

        if not best_text or best_size < 14:   # too small to be a title
            return None

        # Collect all words at ≥90% of the largest font size (multi-word title)
        threshold = best_size * 0.90
        title_words = []
        for page in pdf.pages[:3]:
            try:
                words = page.extract_words(extra_attrs=["size"])
            except Exception:
                continue
            for w in words:
                # remove the `len(text)>2` restriction here so we can grab words like "of", "in" in the title
                if float(w.get("size", 0)) >= threshold:
                    title_words.append(w["text"])

        if title_words:
            candidate = " ".join(title_words)
            # Sanity: should look like a title, not a fragment
            if 2 <= len(candidate.split()) <= 20:
                return candidate.title() if candidate.isupper() else candidate
        return best_text

    # ─────────────────────────────────────────────────────────────────────────
    # Apply PDF document properties
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_pdf_properties(self, metadata: CourseMetadata, doc_info: dict, pdf_path: Path):
        def _get(key): 
            v = doc_info.get(key, "")
            return v.strip() if isinstance(v, str) and v.strip() else None

        if _get('Title'):
            # Only set title if it's not the exact same as the stem or if it's an obviously good title
            title = _get('Title')
            # Some PDFs have metadata title matching the filename stem without caps, ignore those to let heuristic work later
            if title.lower() != pdf_path.stem.lower():
                metadata.title = title

        # Leave author as Unknown if not nicely formatted in PDF properties
        if _get('Author'):     
            metadata.author = _get('Author')
        if _get('Subject'):    
            metadata.subject = _get('Subject')

    # ─────────────────────────────────────────────────────────────────────────
    # Pattern-based inference  (the heart of the improvement)
    # ─────────────────────────────────────────────────────────────────────────

    def _infer_from_text(
        self,
        metadata: CourseMetadata,
        cover_text: str,
        full_text: str,
    ) -> None:
        combined = cover_text + "\n\n" + full_text

        # ── Title (text fallback after font heuristic) ─────────────────────
        if metadata.title in ("Unknown", "", self.course_pdf_path.stem.replace('_',' ').replace('-',' ').title()):
            for pat in _TITLE_PATTERNS:
                m = pat.search(combined)
                if m:
                    metadata.title = m.group(1).strip()
                    break

        # ── Author ────────────────────────────────────────────────────────
        if metadata.author == "Unknown":
            for pat in _AUTHOR_PATTERNS:
                m = pat.search(cover_text)   # prefer cover page
                if m:
                    raw_author = m.group(1).strip().rstrip('.,')
                    # Title-case all-caps names (e.g. "DR. MAHESH S. RAISINGHANI" → "Dr. Mahesh S. Raisinghani")
                    if raw_author == raw_author.upper():
                        raw_author = raw_author.title()
                    metadata.author = raw_author
                    logger.info(f"Inferred author: {metadata.author}")
                    break
            if metadata.author == "Unknown":
                for pat in _AUTHOR_PATTERNS:
                    m = pat.search(full_text)
                    if m:
                        raw_author = m.group(1).strip().rstrip('.,')
                        if raw_author == raw_author.upper():
                            raw_author = raw_author.title()
                        metadata.author = raw_author
                        break

        # ── Contributing authors (collect all, deduplicate) ────────────────
        if not metadata.contributing_authors:
            found = []
            contrib_pat = re.compile(
                r'Contributing\s+Authors?\s*\n((?:[^\n]+\n){1,20})', re.IGNORECASE
            )
            m = contrib_pat.search(full_text)
            if m:
                block = m.group(1)
                # Each line: "First Last, Institution"
                for line in block.splitlines():
                    line = line.strip()
                    name_m = re.match(r'^([A-Z][a-z]+ [A-Z][^,\n]{1,40})', line)
                    if name_m:
                        found.append(name_m.group(1).strip())
            if found:
                metadata.contributing_authors = found[:15]  # cap at 15

        # ── Subject ───────────────────────────────────────────────────────
        if metadata.subject == "Unknown":
            # Derive from title first
            title_subject = self._subject_from_title(metadata.title)
            if title_subject:
                metadata.subject = title_subject
            else:
                for pat in _SUBJECT_PATTERNS:
                    m = pat.search(combined)
                    if m:
                        metadata.subject = m.group(1).strip().rstrip('.,')
                        break

        # ── Description ───────────────────────────────────────────────────
        if metadata.description == "Unknown":
            for pat in _DESCRIPTION_PATTERNS:
                m = pat.search(combined)   # search cover + full text
                if m:
                    raw = m.group(1).strip()
                    # Skip suspiciously short or all-boilerplate fragments
                    if len(raw) < 20 or raw.lower().startswith(('access for free', 'if you')):
                        continue
                    metadata.description = raw[:400] if len(raw) > 400 else raw
                    logger.info("Inferred description.")
                    break

        # ── Level (introductory / intermediate / advanced) ─────────────────
        if metadata.level == "Unknown":
            level_pat = re.compile(
                r'\b(introductory|introduction|beginner|foundational?|'
                r'intermediate|advanced|graduate-level|undergraduate)\b', re.IGNORECASE
            )
            m = level_pat.search(combined)
            if m:
                raw = m.group(1).lower()
                if raw in ('introductory','introduction','beginner','foundational','foundations'):
                    metadata.level = "Introductory"
                elif raw in ('intermediate',):
                    metadata.level = "Intermediate"
                elif raw in ('advanced','graduate-level'):
                    metadata.level = "Advanced"
                else:
                    metadata.level = raw.capitalize()

        # ── Target audience ─────────────────────────────────────────────────
        if metadata.target_audience == "Unknown":
            for pat in _AUDIENCE_PATTERNS:
                m = pat.search(combined)
                if m:
                    metadata.target_audience = m.group(0).strip().rstrip('.,')[:200]
                    break
            # Heuristic: infer from level if regex found nothing
            if metadata.target_audience == "Unknown" and metadata.level != "Unknown":
                level_audience_map = {
                    "Introductory": "Introductory college students or beginners with no prior background",
                    "Intermediate": "Students with foundational knowledge seeking to deepen understanding",
                    "Advanced": "Advanced students or practitioners with prior knowledge",
                    "Undergraduate": "Undergraduate students",
                    "Graduate": "Graduate-level students",
                }
                if metadata.level in level_audience_map:
                    metadata.target_audience = level_audience_map[metadata.level]
                    logger.info(f"Heuristic target_audience from level: {metadata.target_audience}")

        # ── Prerequisites ─────────────────────────────────────────────────
        if not metadata.prerequisites:
            found = []
            for pat in _PREREQ_PATTERNS:
                for m in pat.finditer(full_text):
                    item = m.group(1).strip().rstrip('.,')
                    if item and item not in found:
                        found.append(item)
            if found:
                metadata.prerequisites = found
            else:
                # Many introductory books have NO prerequisites — record that
                if metadata.level == "Introductory":
                    metadata.prerequisites = ["No formal prerequisites required"]

        # ── Learning outcomes — capture full bullet lists ──────────────────
        if not metadata.learning_outcomes:
            metadata.learning_outcomes = self._extract_learning_outcomes(full_text)

        # ── Publisher ─────────────────────────────────────────────────────
        if metadata.publisher == "Unknown":
            for pat in _PUBLISHER_PATTERNS:
                m = pat.search(combined)
                if m:
                    metadata.publisher = m.group(1).strip().rstrip('.,')[:100]
                    break

        # ── ISBN ──────────────────────────────────────────────────────────
        if metadata.isbn == "Unknown":
            m = _ISBN_PATTERN.search(combined)
            if m:
                metadata.isbn = m.group(1).strip()

        # ── Year ──────────────────────────────────────────────────────────
        if metadata.year == "Unknown":
            years = _YEAR_PATTERN.findall(combined[:2000])  # near top
            if years:
                # Pick the most recent plausible publication year
                metadata.year = max(years)

    # ─────────────────────────────────────────────────────────────────────────
    # Learning outcome multi-bullet extractor
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_learning_outcomes(self, text: str, max_outcomes: int = 20) -> List[str]:
        """
        Captures all bullet points following a 'Learning Objectives' / 
        'By the end of this section' header across the scanned pages.
        """
        outcomes = []
        # Find every "Learning Objectives" block
        section_pat = re.compile(
            r'(?:Learning\s+Objectives?|By\s+the\s+end\s+of\s+this\s+(?:section|chapter)'
            r'|Upon\s+completion|After\s+completing\s+this)',
            re.IGNORECASE
        )
        bullet_pat = re.compile(
            r'(?:^|\n)\s*[•\-\*·]\s*(.+?)(?=\n\s*[•\-\*·]|\n\n|\Z)',
            re.DOTALL
        )
        # Also "• verb object" style without explicit header
        verb_pat = re.compile(
            r'(?:^|\n)\s*[•\-\*·]\s*(?:Define|Describe|Explain|Identify|Discuss|'
            r'Apply|Analyze|Evaluate|Compare|Demonstrate|Understand)\s+(.{10,200})',
            re.IGNORECASE | re.MULTILINE
        )

        for m_section in section_pat.finditer(text):
            start = m_section.end()
            chunk = text[start:start + 800]  # scan 800 chars after header
            for m_bullet in bullet_pat.finditer(chunk):
                item = m_bullet.group(1).strip().replace('\n', ' ')
                if 5 < len(item) < 250 and item not in outcomes:
                    outcomes.append(item)
                if len(outcomes) >= max_outcomes:
                    return outcomes

        # Fallback: action-verb bullet scan if no header found
        if not outcomes:
            for m in verb_pat.finditer(text):
                item = m.group(0).strip().lstrip('•-*· ').replace('\n', ' ')
                if item not in outcomes:
                    outcomes.append(item)
                if len(outcomes) >= max_outcomes:
                    break

        return outcomes

    # ─────────────────────────────────────────────────────────────────────────
    # Subject from title
    # ─────────────────────────────────────────────────────────────────────────

    def _subject_from_title(self, title: str) -> Optional[str]:
        """
        'Foundations of Information Systems' → 'Information Systems'
        'Introduction to Machine Learning' → 'Machine Learning'
        """
        m = re.match(
            r'(?:introduction\s+to|foundations?\s+(?:of|in)|'
            r'fundamentals?\s+of|principles\s+of|essentials?\s+of|'
            r'beginning|learning)\s+(.+)',
            title, re.IGNORECASE
        )
        return m.group(1).strip() if m else None

    # ─────────────────────────────────────────────────────────────────────────
    # Optional LLM fallback
    # ─────────────────────────────────────────────────────────────────────────

    def _llm_fill(self, metadata: CourseMetadata, sample_text: str) -> None:
        """
        Call a lightweight LLM to fill any still-unknown fields.
        `self.llm_caller` should be a callable(prompt: str) -> str.
        Uses structured JSON output request for reliability.
        """
        unknown_fields = [
            f for f in ('title','author','subject','target_audience',
                        'description','level')
            if getattr(metadata, f) == "Unknown"
        ]
        if not unknown_fields:
            return

        prompt = (
            f"You are a metadata extractor. Given this excerpt from a course book, "
            f"extract the following fields as JSON with no extra text:\n"
            f"{unknown_fields}\n\n"
            f"Excerpt:\n{sample_text}\n\n"
            f"Return only valid JSON, e.g.: {{\"title\": \"...\", \"author\": \"...\"}}"
        )
        try:
            response = self.llm_caller(prompt)
            # Strip markdown code fences if present
            clean = re.sub(r'```(?:json)?|```', '', response).strip()
            data = json.loads(clean)
            for field in unknown_fields:
                if field in data and data[field] and data[field] != "Unknown":
                    setattr(metadata, field, data[field])
                    logger.info(f"LLM filled '{field}': {data[field]}")
        except Exception as e:
            logger.warning(f"LLM fallback failed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Parsers (unchanged from v1)
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_url(self) -> CourseMetadata:
        try:
            req = urllib.request.Request(self.metadata_source, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read().decode('utf-8', errors='ignore')
                if content.strip().startswith('{'):
                    try:
                        return CourseMetadata(**{k: v for k, v in json.loads(content).items()
                                                  if k in CourseMetadata.model_fields})
                    except Exception:
                        pass
                meta = CourseMetadata(source=self.metadata_source)
                self._infer_from_text(meta, content[:500], content)
                return meta
        except Exception as e:
            logger.error(f"URL fetch failed: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_json(self, path: Path) -> CourseMetadata:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return CourseMetadata(**{k: v for k, v in data.items() if k in CourseMetadata.model_fields})
        except Exception as e:
            logger.error(f"JSON parse failed: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_txt(self, path: Path) -> CourseMetadata:
        data = {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if ':' in line:
                        key, val = line.split(':', 1)
                        k = key.strip().lower().replace(' ', '_')
                        if k in ('prerequisites', 'learning_outcomes', 'contributing_authors'):
                            data[k] = [v.strip() for v in val.split(',')]
                        else:
                            data[k] = val.strip()
            return CourseMetadata(**{k: v for k, v in data.items() if k in CourseMetadata.model_fields})
        except Exception as e:
            logger.error(f"TXT parse failed: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_html(self, path: Path) -> CourseMetadata:
        logger.warning(f"HTML parsing not implemented for {path.name}.")
        return CourseMetadata(source=self.course_pdf_path.name)

# ── Standalone CLI ───────────────────────────────────────────────────────
# Usage:
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --output out.json
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --output out.json --ai
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --output out.json --ai --model gemini
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --metadata https://example.com/meta.json --output out.json

if __name__ == "__main__":
    import argparse
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Standalone Metadata Extractor — extract course metadata to a reviewable JSON file."
    )
    parser.add_argument("--pdf",      type=str, required=True,
                        help="Path to the course PDF.")
    parser.add_argument("--metadata", type=str, default=None,
                        help="Optional explicit path or URL to an external metadata source.")
    parser.add_argument("--output",   type=str, required=True,
                        help="Path to save the extracted JSON metadata for review.")
    parser.add_argument("--ai",       action="store_true", default=False,
                        help="Enable AI extraction (Claude → Gemini fallback) to fill missing fields.")
    parser.add_argument("--model",    type=str, default="claude", choices=["claude", "gemini"],
                        help="Preferred AI model (default: claude). Only used when --ai is set.")

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error(f"PDF file not found: {pdf_path}")
        sys.exit(1)

    ingestor = MetadataIngestor(
        course_pdf_path=pdf_path,
        metadata_source=args.metadata,
        use_ai=args.ai,
        preferred_model=args.model,
    )
    metadata = ingestor.ingest()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(metadata.model_dump_json(indent=2))

    print(f"\n✅ Metadata saved to: {output_path}")
    print("   Review and edit the JSON, then pass it to the evaluator:")
    print(f"   python3 -m src.main --input <courses_dir> --output <output_dir> --metadata {output_path}")

    print("\n── Extraction summary ──────────────────────────────────")
    fields = [
        ("title",            metadata.title),
        ("author",           metadata.author),
        ("subject",          metadata.subject),
        ("level",            metadata.level),
        ("year",             metadata.year),
        ("isbn",             metadata.isbn),
        ("publisher",        metadata.publisher),
        ("target_audience",  metadata.target_audience),
        ("description",      (metadata.description[:60] + "…")
                              if len(metadata.description) > 60
                              else metadata.description),
        ("prerequisites",    f"{len(metadata.prerequisites)} item(s)"),
        ("learning_outcomes",f"{len(metadata.learning_outcomes)} item(s)"),
        ("contributing_authors", f"{len(metadata.contributing_authors)} item(s)"),
    ]
    for label, value in fields:
        status = "⚠ " if value in ("Unknown", "0 item(s)") else "✓ "
        print(f"   {status}{label:<22} {value}")
    print("────────────────────────────────────────────────────────")