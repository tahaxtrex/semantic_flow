"""Metadata extraction for course PDFs — two-phase pipeline (ADR-038).

Phase 1 (heuristic, deterministic): pdfplumber reads the first 15 pages into a
structured intermediate dict. Author/publisher candidates and the font-heuristic
title are scoped strictly to the first 3 cover pages. A TOC parser detects
dotted-leader / indented-hierarchy chapter listings.

Phase 2 (LLM enrichment, single call): Gemini 2.5 Flash (primary) or Claude
Sonnet 4.6 (fallback) receives the raw 15-page text and the cover text, and
returns a strict JSON object matching the `CourseMetadata` schema. The system
prompt forbids hallucination and scopes author/publisher to the cover text.

Phase 3 (validation + merge): the Pydantic model enforces the `level` enum and
coerces author/publisher values longer than 6 words to empty strings (structural
defense against body-text corruption). `_merge_heuristic_and_llm()` fills any
still-empty scalar with the heuristic candidate.
"""

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
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ── Regex helpers (shared by heuristic phase and legacy fallback) ─────────────

_TITLE_PATTERNS = [
    re.compile(r'^title\s*[:\-]\s*(.{5,120})', re.IGNORECASE | re.MULTILINE),
    re.compile(r'About\s+([A-Z][^.\n]{10,100})\n', re.MULTILINE),
]

_AUTHOR_PATTERNS = [
    re.compile(r'(?:senior\s+)?contributing\s+author[s]?\s*\n\s*(?:DR\.?|PROF\.?)?\s*([A-Z]{2,}(?:\s+[A-Z\.]{1,5})*(?:\s+[A-Z]{2,})+)', re.IGNORECASE),
    re.compile(r'\bby\s+([A-Z][a-z]+(?:\s+[A-Z]\.?\s*)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', re.MULTILINE),
    re.compile(r'(?:author|written by|prepared by)\s*[:\-]\s*([A-Z][^.\n]{5,80})', re.IGNORECASE),
    re.compile(r'(?:senior\s+)?contributing\s+author[s]?\s*\n\s*((?:Dr\.|Prof\.)?\s*[A-Z][^.\n]{5,60})', re.IGNORECASE),
    re.compile(r'(?:author|by)[:\s]+(?:Dr\.|Prof\.)?\s*([A-Z][a-z]+(?:\s+[A-Z]\.?\s*[A-Z][a-z]+)+)', re.IGNORECASE),
]

_PUBLISHER_PATTERNS = [
    re.compile(r'[Pp]ublished\s+by\s+([^.\n]{5,80})'),
    re.compile(r'[Cc]opyright\s+(?:\d{4}\s+)?([A-Z][^.\n]{5,60}(?:Press|Publishing|University|Inc\.|LLC|College))'),
    re.compile(r'©\s*\d{4}\s+([A-Z][^.\n]{5,60}(?:Press|Publishing|University|Inc\.|LLC|College))'),
    re.compile(r'\b(OpenStax(?:\s+College)?)\b', re.IGNORECASE),
    re.compile(r'^([A-Z][^.\n]{3,50}(?:Press|Publishing|Books|Education))', re.MULTILINE),
]

_ISBN_PATTERN = re.compile(
    r'ISBN(?:-1[03])?\s*[:\-]?\s*((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])', re.IGNORECASE
)
_YEAR_PATTERN = re.compile(r'\b(20[0-2]\d|19[89]\d)\b')

_SUBJECT_PATTERNS = [
    re.compile(r'course\s+(?:in|on)\s+([A-Z][^.,]{3,60})', re.IGNORECASE),
    re.compile(r'textbook\s+(?:for|in|on)\s+([A-Z][^.,]{3,60})', re.IGNORECASE),
    re.compile(r'^(?:introduction\s+to|foundations?\s+(?:of|in)|'
               r'fundamentals?\s+of)\s+([A-Z][^.\n]{3,80})',
               re.IGNORECASE | re.MULTILINE),
]

_DESCRIPTION_PATTERNS = [
    re.compile(r'About\s+[\w\s]+?\n\s*([A-Z][^.]{30,400}\.)', re.DOTALL),
    re.compile(
        r'(?:this\s+(?:book|textbook|text|course|resource))\s+'
        r'(?:provides?|covers?|introduces?|is\s+designed\s+to|aims?\s+to)\s+'
        r'([^.]{30,350}\.)', re.IGNORECASE
    ),
    re.compile(r'(?:Preface|Introduction)\s*\n+([A-Z][^\n]{50,400})', re.DOTALL),
]

# OS/tool-generated usernames that should never be treated as real authors
_GARBAGE_AUTHOR_RE = re.compile(
    r'^(windows\s+user|administrator|admin|user|root|unknown|default|test'
    r'|owner|pc|laptop|desktop|student|author|anonymous|n/?a)$',
    re.IGNORECASE,
)
_AUTHOR_ARTEFACT_RE = re.compile(
    r'(acrobat|microsoft\s+word|libreoffice|openoffice|latex|tex|wkhtmltopdf'
    r'|created\s+by|generated\s+by|converted\s+by)',
    re.IGNORECASE,
)
_COURSE_CODE_RE = re.compile(
    r'\s*[\[\(]?\s*[A-Z]{1,5}[-\s]?\d{3,}[A-Z0-9]*\s*[\]\)]?\s*', re.IGNORECASE
)
_TITLE_LABEL_RE = re.compile(
    r'\s*[-–|:]\s*(lecture\s+notes?|syllabus|study\s+material|class\s+notes?'
    r'|course\s+material|handout|notes?)\s*$',
    re.IGNORECASE,
)

# ── TOC heuristic regex (ADR-038) ─────────────────────────────────────────────
# Matches lines like:
#   "1. Introduction ........... 15"
#   "Chapter 1 Compilation ...... 1"
#   "Preface ........... iii"
#   "   1.2 Sub-topic ............ 23"
_TOC_LINE_RE = re.compile(
    r"^\s*"
    r"(?P<num>(?:\d+(?:\.\d+)*|[IVXivx]+|Chapter\s+\d+|Ch\.\s*\d+|Part\s+[IVX]+))?"
    r"\s*\.?\s*"
    r"(?P<title>[A-Za-z][^.\n]*?)"
    r"\s*[\.\s]{3,}\s*"
    r"(?P<page>\d{1,4}|[ivxlcdm]+)\s*$",
    re.IGNORECASE,
)

_TOC_HEADING_RE = re.compile(
    r"^\s*(contents|table\s+of\s+contents|index)\s*$", re.IGNORECASE
)

_ALLOWED_LEVELS = {
    "",
    "introductory",
    "intermediate",
    "advanced",
    "undergraduate_introductory",
    "undergraduate_advanced",
    "graduate",
}


# ── Pydantic models ───────────────────────────────────────────────────────────


class TOCEntry(BaseModel):
    """A single table-of-contents entry parsed from the heuristic TOC scan
    or returned by the LLM (ADR-038)."""

    chapter_number: Optional[str] = None
    title: str = ""
    page_number: Optional[int] = None


class CourseMetadata(BaseModel):
    """Schema-complete course metadata (ADR-038).

    Core fields populated by the two-phase heuristic + LLM pipeline:
      - `title`, `author`, `publisher`: scoped strictly to the first 3 cover pages.
      - `level`: enum validated.
      - `target_audience`: freeform string.
      - `prerequisites_stated` / `prerequisites_inferred`: explicit vs prose-derived.
      - `learning_outcomes_stated` / `learning_outcomes_inferred`: same split.
      - `toc`: parsed table of contents.
      - `draft_notes`: LLM's freeform notes about the book's state
        (draft, revision, v0.x.y, etc.) for downstream audit.

    Legacy fields retained for backwards compatibility with older JSON fixtures
    and external metadata files (`source`, `subject`, `description`, `isbn`,
    `year`, `contributing_authors`). The legacy flat `prerequisites` /
    `learning_outcomes` fields are **deleted** (Q-030).
    """

    title: str = ""
    author: str = ""
    publisher: str = ""
    level: str = ""
    target_audience: str = ""
    prerequisites_stated: List[str] = Field(default_factory=list)
    prerequisites_inferred: List[str] = Field(default_factory=list)
    learning_outcomes_stated: List[str] = Field(default_factory=list)
    learning_outcomes_inferred: List[str] = Field(default_factory=list)
    toc: List[TOCEntry] = Field(default_factory=list)
    draft_notes: str = ""

    # Legacy fields retained for backwards compat
    source: str = ""
    subject: str = ""
    description: str = ""
    isbn: str = ""
    year: str = ""
    contributing_authors: List[str] = Field(default_factory=list)

    @field_validator("level", mode="before")
    @classmethod
    def _validate_level(cls, v):
        if v is None:
            return ""
        v_str = str(v).strip().lower().replace(" ", "_").replace("-", "_")
        return v_str if v_str in _ALLOWED_LEVELS else ""

    @field_validator("author", "publisher", mode="before")
    @classmethod
    def _validate_name_length(cls, v):
        """Structural defense against body-text corruption: reject values
        longer than 6 words (ADR-038)."""
        if v is None:
            return ""
        v_str = str(v).strip()
        if len(v_str.split()) > 6:
            logger.debug(
                f"Rejecting author/publisher candidate (>6 words): {v_str!r}"
            )
            return ""
        return v_str

    @field_validator("toc", mode="before")
    @classmethod
    def _coerce_toc(cls, v):
        """Accept both `List[TOCEntry]` and `List[dict]` (for LLM responses
        that don't use the Pydantic type directly)."""
        if v is None:
            return []
        return v


# ── LLM enrichment prompt (ADR-038, single call) ──────────────────────────────

_METADATA_SYSTEM_PROMPT = """
You are an expert course cataloguer. You will be given raw text extracted from
the first 15 pages of a textbook PDF, plus a separately-labeled COVER TEXT block
containing only the first 3 pages. Return a single JSON object describing the
course.

HARD RULES — FAILURE TO FOLLOW THESE IS A CRITICAL BUG:

1. NEVER INVENT CONTENT. If a field cannot be determined confidently from the
   provided text, return an empty string "" for scalars or an empty array [] for
   lists. Do not guess. Do not draw on external knowledge.

2. AUTHOR AND PUBLISHER COME FROM COVER TEXT ONLY. The author and publisher
   fields must be extracted exclusively from the `<cover_text>` block (the first
   3 pages). If they are not present there, return "". NEVER pull author or
   publisher from the 15-page raw text block.

3. STATED vs INFERRED:
   - `*_stated` lists: items explicitly labeled under a heading such as
     "Learning Objectives", "Learning Outcomes", "Course Goals", "Prerequisites",
     "Prior Knowledge", "Required Background", etc. The author WROTE them as a
     list, typically with bullets or numbering.
   - `*_inferred` lists: items implied by preface / introduction / about-this-
     book prose. You read the paragraph and summarise the pedagogical goals or
     assumed prerequisites in your own words.
   If a book states its outcomes in prose only, the `_stated` list is empty and
   the `_inferred` list is populated. If a book has explicit labeled lists, the
   `_stated` list is populated; the `_inferred` list may still be populated if
   the prose adds further goals beyond the labeled list.

4. LEVEL must be one of exactly these strings (or "" if uncertain):
   "introductory", "intermediate", "advanced", "undergraduate_introductory",
   "undergraduate_advanced", "graduate".

5. TOC entries: extract ONLY top-level chapters into the `toc` array. Each
   entry is `{"chapter_number": "1", "title": "...", "page_number": 15}`.
   `chapter_number` may be null. `page_number` may be null if not visible.
   CRITICAL: never include sub-sections or subsections — skip any entry whose
   number contains a dot (e.g. "1.1", "2.3", "10.5"). Valid top-level numbers
   look like "1", "2", "Chapter 1", "Part I". If the TOC page lists only
   sub-sections (e.g. "1.1 … 3", "1.2 … 5") infer the top-level chapters from
   the chapter headings found in the raw text instead. If no chapters are
   identifiable, return an empty array.

6. DRAFT_NOTES: if the book self-identifies as a draft, work-in-progress,
   pre-release, or carries a version number like "v0.x", note it here as one
   sentence. Otherwise "".

Required JSON schema (return exactly this shape, no markdown fences):
{
  "title": "string",
  "author": "string",
  "publisher": "string",
  "level": "string",
  "target_audience": "string",
  "prerequisites_stated": ["string"],
  "prerequisites_inferred": ["string"],
  "learning_outcomes_stated": ["string"],
  "learning_outcomes_inferred": ["string"],
  "toc": [{"chapter_number": "string or null", "title": "string", "page_number": 0}],
  "draft_notes": "string"
}
""".strip()


# ── AI metadata extractor (single-call, Gemini primary / Claude fallback) ─────


class AIMetadataExtractor:
    """Single-call metadata enrichment (ADR-038, Q-029).

    Gemini 2.5 Flash is the primary provider — its native `response_schema`
    + `response_mime_type="application/json"` give strong structural
    guarantees for the schema. Claude Sonnet 4.6 is the fallback, used only
    when `GEMINI_API_KEY` is missing or the Gemini call fails.

    One call per PDF. No second-pass list-field extraction (that path is
    deleted; the new schema captures stated/inferred splits in one shot).
    """

    CLAUDE_MODEL = "claude-sonnet-4-6"
    GEMINI_MODEL = "gemini-2.5-flash"
    MAX_RETRIES = 2
    RETRY_BACKOFF = 3  # seconds, doubles each retry
    MAX_OUTPUT_TOKENS = 4096

    def __init__(
        self,
        anthropic_key: Optional[str] = None,
        gemini_key: Optional[str] = None,
    ):
        self._anthropic_client = None
        self._gemini_client = None

        if gemini_key:
            try:
                from google import genai

                self._gemini_client = genai.Client(api_key=gemini_key)
            except ImportError:
                logger.warning(
                    "google-genai package not installed — Gemini unavailable."
                )

        if anthropic_key:
            try:
                from anthropic import Anthropic

                self._anthropic_client = Anthropic(api_key=anthropic_key)
            except ImportError:
                logger.warning(
                    "anthropic package not installed — Claude unavailable."
                )

    @property
    def available(self) -> bool:
        return self._gemini_client is not None or self._anthropic_client is not None

    # ── Public entry ──────────────────────────────────────────────────────

    def extract(
        self,
        raw_text_15: str,
        cover_text: str,
    ) -> Optional[CourseMetadata]:
        """Single-call metadata enrichment. Returns ``None`` if both providers
        fail or neither is configured."""
        if not self.available:
            logger.info(
                "AI metadata extractor: no API keys configured; skipping LLM phase."
            )
            return None

        user_prompt = self._build_user_prompt(raw_text_15, cover_text)

        # Gemini first (ADR-038, Q-029)
        response_text = self._try_gemini(user_prompt)
        if response_text is None:
            logger.info(
                "AI metadata: Gemini unavailable or failed; falling back to Claude."
            )
            response_text = self._try_claude(user_prompt)

        if response_text is None:
            logger.error("AI metadata: all providers failed.")
            return None

        return self._parse_response(response_text)

    # ── Prompt construction ───────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(raw_text_15: str, cover_text: str) -> str:
        # Cap sizes conservatively to stay well within context limits.
        cover_trimmed = cover_text[:6000]
        raw_trimmed = raw_text_15[:15000]
        return (
            "<cover_text>\n"
            f"{cover_trimmed}\n"
            "</cover_text>\n\n"
            "<raw_text_first_15_pages>\n"
            f"{raw_trimmed}\n"
            "</raw_text_first_15_pages>\n\n"
            "Return the JSON object now. Remember: never invent content; "
            "author and publisher come from <cover_text> only."
        )

    # ── Providers ─────────────────────────────────────────────────────────

    def _try_gemini(self, user_prompt: str) -> Optional[str]:
        if self._gemini_client is None:
            return None
        try:
            from google.genai import types as genai_types
        except ImportError:
            return None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"AI metadata: calling Gemini (attempt {attempt})."
                )
                resp = self._gemini_client.models.generate_content(
                    model=self.GEMINI_MODEL,
                    contents=_METADATA_SYSTEM_PROMPT + "\n\n" + user_prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.0,
                        response_mime_type="application/json",
                        max_output_tokens=self.MAX_OUTPUT_TOKENS,
                    ),
                )
                return resp.text
            except Exception as e:
                wait = self.RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    f"Gemini attempt {attempt} failed: {e}. Waiting {wait}s."
                )
                if attempt < self.MAX_RETRIES:
                    time.sleep(wait)
        return None

    def _try_claude(self, user_prompt: str) -> Optional[str]:
        if self._anthropic_client is None:
            return None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"AI metadata: calling Claude (attempt {attempt})."
                )
                resp = self._anthropic_client.messages.create(
                    model=self.CLAUDE_MODEL,
                    max_tokens=self.MAX_OUTPUT_TOKENS,
                    temperature=0.0,
                    system=_METADATA_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return resp.content[0].text
            except Exception as e:
                wait = self.RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    f"Claude attempt {attempt} failed: {e}. Waiting {wait}s."
                )
                if attempt < self.MAX_RETRIES:
                    time.sleep(wait)
        return None

    # ── Response parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_response(text: str) -> Optional[CourseMetadata]:
        clean = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(
                f"AI metadata response was not valid JSON: {e}\nRaw: {text[:300]}"
            )
            return None

        # Coerce null/missing scalars to ""; null/missing lists to []
        scalar_fields = (
            "title",
            "author",
            "publisher",
            "level",
            "target_audience",
            "draft_notes",
        )
        list_fields = (
            "prerequisites_stated",
            "prerequisites_inferred",
            "learning_outcomes_stated",
            "learning_outcomes_inferred",
        )
        for fld in scalar_fields:
            if data.get(fld) is None:
                data[fld] = ""
        for fld in list_fields:
            if not isinstance(data.get(fld), list):
                data[fld] = []
            else:
                # clean newlines and strip empties
                data[fld] = [
                    str(x).strip().replace("\n", " ")
                    for x in data[fld]
                    if str(x).strip()
                ]

        # toc normalisation
        raw_toc = data.get("toc") or []
        clean_toc: List[dict] = []
        if isinstance(raw_toc, list):
            for entry in raw_toc:
                if not isinstance(entry, dict):
                    continue
                ch = entry.get("chapter_number")
                title = str(entry.get("title", "")).strip()
                page = entry.get("page_number")
                if not title:
                    continue
                try:
                    page_int = int(page) if page is not None else None
                except (TypeError, ValueError):
                    page_int = None
                clean_toc.append(
                    {
                        "chapter_number": str(ch) if ch is not None else None,
                        "title": title,
                        "page_number": page_int,
                    }
                )
        data["toc"] = clean_toc

        # Drop any keys that are not part of the schema before constructing
        filtered = {k: v for k, v in data.items() if k in CourseMetadata.model_fields}
        try:
            return CourseMetadata(**filtered)
        except Exception as e:
            logger.error(f"CourseMetadata validation failed: {e}")
            return None


# ── Heuristic phase (ADR-038) ─────────────────────────────────────────────────


def _extract_toc_heuristic(pages: list) -> List[Dict[str, Any]]:
    """Parse a 'Contents' / 'Table of Contents' page from the first 15 pages
    into a list of ``{chapter_number, title, page_number}`` dicts.

    Detection strategy:
      1. Find a page whose text starts with a "Contents" or "Table of Contents"
         heading (or has very few non-TOC lines).
      2. For each subsequent line on that page (and the next 1-2 pages), try
         ``_TOC_LINE_RE``. A line matches if it has a dotted-leader or multi-
         space gap followed by a trailing page number.
      3. Collect matches until we encounter a page without any matches.
    """
    entries: List[Dict[str, Any]] = []

    # Find the first TOC-looking page
    toc_start = None
    for idx, page in enumerate(pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        first_lines = [ln for ln in text.splitlines() if ln.strip()][:5]
        if not first_lines:
            continue
        if any(_TOC_HEADING_RE.match(ln) for ln in first_lines):
            toc_start = idx
            break

    if toc_start is None:
        return []

    # Parse up to 3 consecutive pages starting at toc_start
    roman_map = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

    def _coerce_page_number(raw: str) -> Optional[int]:
        raw = raw.strip().lower()
        if raw.isdigit():
            try:
                return int(raw)
            except ValueError:
                return None
        # roman numeral
        total = 0
        prev = 0
        for ch in reversed(raw):
            val = roman_map.get(ch)
            if val is None:
                return None
            if val < prev:
                total -= val
            else:
                total += val
            prev = val
        return total if total > 0 else None

    consecutive_empty = 0
    for idx in range(toc_start, min(toc_start + 4, len(pages))):
        try:
            text = pages[idx].extract_text() or ""
        except Exception:
            continue
        page_matches = 0
        for line in text.splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            if _TOC_HEADING_RE.match(line):
                continue
            m = _TOC_LINE_RE.match(line)
            if not m:
                continue
            title = (m.group("title") or "").strip().rstrip(".").strip()
            if not title or len(title) > 200:
                continue
            # skip lines that look like a running header ("Chapter 1" alone with a
            # large spacing)
            if title.lower() in ("contents", "table of contents", "index"):
                continue
            page_num = _coerce_page_number(m.group("page") or "")
            chapter_num = (m.group("num") or "").strip() or None
            # Skip sub-chapters (e.g. "1.1", "0.1", "2.3.4") — top-level only
            if chapter_num and "." in chapter_num:
                continue
            entries.append(
                {
                    "chapter_number": chapter_num,
                    "title": title,
                    "page_number": page_num,
                }
            )
            page_matches += 1

        if page_matches == 0 and entries:
            consecutive_empty += 1
            if consecutive_empty >= 1:
                break
        else:
            consecutive_empty = 0

    return entries


def _first_match(patterns: List[re.Pattern], text: str) -> Optional[str]:
    for pat in patterns:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip().rstrip(".,;:")
            if raw == raw.upper():
                raw = raw.title()
            return raw
    return None


def _extract_heuristic_metadata(pdf_path: Path) -> Dict[str, Any]:
    """Heuristic phase (ADR-038) — produces the intermediate dict the LLM
    phase consumes.

    Returns:
        ``{
            "raw_text_15": str,      # full text of first 15 pages
            "cover_text":  str,      # first 3 pages only
            "title":       str,      # font-heuristic title (may be "")
            "author_candidate":    str,   # from cover_text only
            "publisher_candidate": str,   # from cover_text only
            "toc_candidates":      list,  # from _extract_toc_heuristic
            "pdf_properties":      dict,  # PDF doc metadata (Title/Author/Subject)
            "legacy_subject":      str,
            "legacy_description":  str,
            "legacy_year":         str,
            "legacy_isbn":         str,
        }``
    """
    result: Dict[str, Any] = {
        "raw_text_15": "",
        "cover_text": "",
        "title": "",
        "author_candidate": "",
        "publisher_candidate": "",
        "toc_candidates": [],
        "pdf_properties": {},
        "legacy_subject": "",
        "legacy_description": "",
        "legacy_year": "",
        "legacy_isbn": "",
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            result["pdf_properties"] = pdf.metadata or {}

            first_15 = pdf.pages[:15]
            cover_pages = pdf.pages[:3]

            # Cover text (for author / publisher / font-title)
            cover_parts: List[str] = []
            for page in cover_pages:
                text = page.extract_text() or ""
                if not text.strip():
                    try:
                        words = page.extract_words()
                        text = " ".join(w["text"] for w in words)
                    except Exception:
                        text = ""
                cover_parts.append(text)
            result["cover_text"] = "\n".join(cover_parts)

            # Raw 15-page text (body-cropped to remove headers/footers)
            raw_parts: List[str] = []
            for page in first_15:
                try:
                    H, W = float(page.height), float(page.width)
                    body = page.within_bbox((0, H * 0.08, W, H * 0.93))
                    words = body.extract_words()
                    raw_parts.append(" ".join(w["text"] for w in words))
                except Exception:
                    raw_parts.append(page.extract_text() or "")
            result["raw_text_15"] = "\n".join(raw_parts)

            # pdftotext fallback for CIDFont/Type3 PDFs
            if len(result["raw_text_15"].split()) < 2000:
                poppler_text = _pdftotext_fallback(pdf_path, max_pages=15)
                if poppler_text and len(poppler_text) > len(result["raw_text_15"]):
                    logger.info(
                        f"pdfplumber extracted unusually little text for "
                        f"'{pdf_path.name}'. Using pdftotext (Poppler) fallback."
                    )
                    result["raw_text_15"] = poppler_text
                    result["cover_text"] = poppler_text[:3000]

            # Font-heuristic title (cover-only)
            result["title"] = _extract_title_by_font(cover_pages) or ""

            # Author / publisher candidates — COVER ONLY
            cover_text = result["cover_text"]
            author_candidate = _first_match(_AUTHOR_PATTERNS, cover_text) or ""
            publisher_candidate = _first_match(_PUBLISHER_PATTERNS, cover_text) or ""

            # Filter garbage authors
            if author_candidate:
                lowered = author_candidate.lower()
                if (
                    _GARBAGE_AUTHOR_RE.match(lowered)
                    or _AUTHOR_ARTEFACT_RE.search(lowered)
                    or " " not in author_candidate
                ):
                    author_candidate = ""
            if len(author_candidate.split()) > 6:
                author_candidate = ""
            if len(publisher_candidate.split()) > 6:
                publisher_candidate = ""

            result["author_candidate"] = author_candidate
            result["publisher_candidate"] = publisher_candidate

            # Heuristic TOC
            result["toc_candidates"] = _extract_toc_heuristic(first_15)

            # Legacy fields for backwards compat (subject/description/year/isbn)
            combined = result["cover_text"] + "\n\n" + result["raw_text_15"]

            for pat in _SUBJECT_PATTERNS:
                m = pat.search(combined)
                if m:
                    result["legacy_subject"] = m.group(1).strip().rstrip(".,")
                    break

            for pat in _DESCRIPTION_PATTERNS:
                m = pat.search(combined)
                if m:
                    raw = m.group(1).strip()
                    if len(raw) >= 30 and not raw.lower().startswith(
                        ("access for free", "if you")
                    ):
                        result["legacy_description"] = raw[:400]
                        break

            m = _ISBN_PATTERN.search(combined)
            if m:
                result["legacy_isbn"] = m.group(1).strip()

            years = _YEAR_PATTERN.findall(combined[:2000])
            if years:
                result["legacy_year"] = max(years)

    except Exception as e:
        logger.error(f"Heuristic metadata extraction failed for {pdf_path}: {e}")

    return result


def _extract_title_by_font(pages) -> Optional[str]:
    """Find the largest-font text block on the given cover pages.
    Works for OpenStax, Pearson, Springer, O'Reilly, etc.
    """
    best_size = 0.0
    best_text: Optional[str] = None

    page_list = list(pages)

    for page in page_list:
        try:
            words = page.extract_words(extra_attrs=["size"])
        except Exception:
            continue
        for w in words:
            try:
                size = float(w.get("size", 0))
            except (TypeError, ValueError):
                continue
            text = (w.get("text") or "").strip()
            if size > best_size and len(text) >= 2 and text.isascii():
                best_size = size
                best_text = text

    if not best_text or best_size < 14:
        return None

    threshold = best_size * 0.90
    title_words: List[str] = []
    for page in page_list:
        try:
            words = page.extract_words(extra_attrs=["size"])
        except Exception:
            continue
        for w in words:
            try:
                size = float(w.get("size", 0))
            except (TypeError, ValueError):
                continue
            if size >= threshold:
                title_words.append(w.get("text", ""))

    if title_words:
        candidate = " ".join(title_words)
        if 2 <= len(candidate.split()) <= 20:
            return candidate.title() if candidate.isupper() else candidate
    return best_text


def _pdftotext_fallback(pdf_path: Path, max_pages: int = 15) -> str:
    """Fallback text extraction via Poppler's pdftotext CLI utility.

    Some PDFs (e.g. OpenStax LaTeX exports) strip the ToUnicode map and use
    Type3 CIDFonts where glyphs are drawn as paths. `pdfplumber` cannot see
    any Character objects in these files. `pdftotext` has robust heuristic
    mapping for these edge cases.
    """
    import subprocess
    import shutil

    if not shutil.which("pdftotext"):
        logger.debug("pdftotext (Poppler) is not installed; skipping fallback.")
        return ""

    try:
        result = subprocess.run(
            [
                "pdftotext",
                "-f",
                "1",
                "-l",
                str(max_pages),
                str(pdf_path),
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.warning(f"pdftotext fallback failed: {e.stderr.strip()}")
        return ""
    except Exception as e:
        logger.warning(f"pdftotext fallback execution error: {e}")
        return ""


def _apply_pdf_properties(
    metadata: CourseMetadata, doc_info: dict, pdf_path: Path
) -> None:
    """Apply cleaned PDF document properties as a last-ditch fallback."""

    def _get(key):
        v = doc_info.get(key, "")
        return v.strip() if isinstance(v, str) and v.strip() else None

    if not metadata.title and _get("Title"):
        title = _get("Title")
        if title.lower() != pdf_path.stem.lower():
            title = _COURSE_CODE_RE.sub(" ", title).strip()
            title = _TITLE_LABEL_RE.sub("", title).strip()
            title = re.sub(r"\[\s*\]", "", title).strip()
            if len(title) >= 3:
                metadata.title = title

    if not metadata.author and _get("Author"):
        author = _get("Author")
        lowered = author.lower()
        is_garbage = (
            _GARBAGE_AUTHOR_RE.match(lowered)
            or _AUTHOR_ARTEFACT_RE.search(lowered)
            or " " not in author
            or not any(c.isupper() for c in author)
        )
        if not is_garbage and len(author.split()) <= 6:
            metadata.author = author

    if not metadata.subject and _get("Subject"):
        subject = _get("Subject")
        if len(subject) <= 200 and subject.count(";") < 2:
            metadata.subject = subject


# ── Merger (ADR-038) ──────────────────────────────────────────────────────────


def _merge_heuristic_and_llm(
    heuristic: Dict[str, Any],
    llm_meta: Optional[CourseMetadata],
    source: str,
) -> CourseMetadata:
    """Merge the heuristic intermediate dict with the LLM result.

    Strategy:
      - LLM wins by default on every field it populates confidently.
      - If the LLM's author/publisher were zeroed by the validator (>6 words)
        OR were simply empty, fall back to the heuristic candidate.
      - If the LLM's title is empty, use the heuristic font-title.
      - If the LLM returned an empty TOC but the heuristic found one, use
        the heuristic TOC.
      - Legacy fields (subject/description/year/isbn) are always filled from
        the heuristic since the LLM schema does not include them.
    """
    if llm_meta is None:
        llm_meta = CourseMetadata()

    # Title
    if not llm_meta.title and heuristic.get("title"):
        llm_meta.title = heuristic["title"]

    # Author — LLM wins unless empty (including when validator zeroed it)
    if not llm_meta.author and heuristic.get("author_candidate"):
        llm_meta.author = heuristic["author_candidate"]

    # Publisher
    if not llm_meta.publisher and heuristic.get("publisher_candidate"):
        llm_meta.publisher = heuristic["publisher_candidate"]

    # TOC — heuristic fills only if LLM produced nothing
    if not llm_meta.toc and heuristic.get("toc_candidates"):
        llm_meta.toc = [TOCEntry(**c) for c in heuristic["toc_candidates"]]

    # Legacy fields — always from heuristic (LLM does not provide them)
    llm_meta.source = source
    if not llm_meta.subject and heuristic.get("legacy_subject"):
        llm_meta.subject = heuristic["legacy_subject"]
    if not llm_meta.description and heuristic.get("legacy_description"):
        llm_meta.description = heuristic["legacy_description"]
    if not llm_meta.year and heuristic.get("legacy_year"):
        llm_meta.year = heuristic["legacy_year"]
    if not llm_meta.isbn and heuristic.get("legacy_isbn"):
        llm_meta.isbn = heuristic["legacy_isbn"]

    return llm_meta


# ── MetadataIngestor ──────────────────────────────────────────────────────────


class MetadataIngestor:
    """Metadata ingestor (ADR-038).

    Pipeline for PDF extraction:
      1. Heuristic phase — ``_extract_heuristic_metadata`` produces the
         intermediate dict with title / author / publisher / TOC candidates
         and the raw 15-page + cover text blocks.
      2. LLM enrichment phase — ``AIMetadataExtractor.extract`` runs a single
         focused Gemini call (Claude fallback) to fill the stated/inferred
         lists, level, target_audience, and draft_notes.
      3. Merge — ``_merge_heuristic_and_llm`` combines both, with LLM winning
         by default and heuristic filling gaps.
      4. PDF properties — applied last as a final fallback for title/author/
         subject only where still empty.

    External metadata sources (`.json`, `.txt`, `.html`, URL) still bypass the
    PDF pipeline entirely — they are treated as authoritative.
    """

    def __init__(
        self,
        course_pdf_path: Path,
        metadata_source: Optional[str] = None,
        use_ai: bool = True,
        # Backwards-compat kwargs (unused; kept so callers don't break)
        preferred_model: Optional[str] = None,
        llm_fallback: bool = False,
        llm_caller=None,
    ):
        self.metadata_source = str(metadata_source) if metadata_source else None
        self.course_pdf_path = Path(course_pdf_path)
        self.base_name = self.course_pdf_path.stem
        self.dir_path = self.course_pdf_path.parent
        self.use_ai = use_ai
        if preferred_model and preferred_model.lower() != "gemini":
            logger.debug(
                "preferred_model=%r is ignored under ADR-038 (Gemini primary, "
                "Claude fallback).",
                preferred_model,
            )

    # ── Public entry ──────────────────────────────────────────────────────

    def ingest(self) -> CourseMetadata:
        if self.metadata_source:
            return self._from_explicit_source()
        return self._autoscan()

    # ── Source routing ────────────────────────────────────────────────────

    def _from_explicit_source(self) -> CourseMetadata:
        src = self.metadata_source
        if src.startswith("http") or src.startswith("www."):
            return self._parse_url()
        path = Path(src)
        if not path.exists():
            logger.warning(f"Metadata file {path} not found.")
            return CourseMetadata(source=self.course_pdf_path.name)
        ext = path.suffix.lower()
        dispatch = {
            ".json": self._parse_json,
            ".txt": self._parse_txt,
            ".html": self._parse_html,
        }
        return dispatch.get(ext, self._extract_metadata_from_pdf)(path)

    def _autoscan(self) -> CourseMetadata:
        for ext, parser in [
            (".json", self._parse_json),
            (".txt", self._parse_txt),
            (".html", self._parse_html),
        ]:
            p = self.dir_path / f"{self.base_name}{ext}"
            if p.exists():
                logger.info(f"Found external metadata: {p}")
                return parser(p)
        logger.info(
            f"No external metadata for '{self.base_name}'. Falling back to PDF."
        )
        return self._extract_metadata_from_pdf(self.course_pdf_path)

    # ── Core PDF extraction (ADR-038) ─────────────────────────────────────

    def _extract_metadata_from_pdf(self, pdf_path: Path) -> CourseMetadata:
        # Phase 1 — heuristic
        heuristic = _extract_heuristic_metadata(pdf_path)

        # Phase 2 — LLM enrichment (single call, Gemini primary / Claude fallback)
        llm_meta: Optional[CourseMetadata] = None
        if self.use_ai:
            anthropic_key = os.getenv("ANTHROPIC_API_KEY")
            gemini_key = os.getenv("GEMINI_API_KEY")
            if not gemini_key and not anthropic_key:
                logger.warning(
                    "use_ai=True but neither GEMINI_API_KEY nor ANTHROPIC_API_KEY "
                    "is set. Metadata will be heuristic-only."
                )
            else:
                extractor = AIMetadataExtractor(
                    anthropic_key=anthropic_key,
                    gemini_key=gemini_key,
                )
                llm_meta = extractor.extract(
                    raw_text_15=heuristic["raw_text_15"],
                    cover_text=heuristic["cover_text"],
                )
        else:
            logger.info("use_ai=False; skipping LLM metadata enrichment.")

        # Phase 3 — merge
        metadata = _merge_heuristic_and_llm(
            heuristic, llm_meta, source=pdf_path.name
        )

        # Phase 4 — PDF properties as last-ditch fallback
        _apply_pdf_properties(metadata, heuristic.get("pdf_properties", {}), pdf_path)

        return metadata

    # ── External-source parsers ───────────────────────────────────────────

    def _parse_url(self) -> CourseMetadata:
        try:
            req = urllib.request.Request(
                self.metadata_source, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read().decode("utf-8", errors="ignore")
                if content.strip().startswith("{"):
                    try:
                        data = json.loads(content)
                        return CourseMetadata(
                            **{
                                k: v
                                for k, v in data.items()
                                if k in CourseMetadata.model_fields
                            }
                        )
                    except Exception:
                        pass
                return CourseMetadata(source=self.metadata_source)
        except Exception as e:
            logger.error(f"URL fetch failed: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_json(self, path: Path) -> CourseMetadata:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            filtered = {
                k: v for k, v in data.items() if k in CourseMetadata.model_fields
            }
            if "source" not in filtered:
                filtered["source"] = path.name
            return CourseMetadata(**filtered)
        except Exception as e:
            logger.error(f"JSON parse failed: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_txt(self, path: Path) -> CourseMetadata:
        data: Dict[str, Any] = {}
        list_keys = {
            "prerequisites_stated",
            "prerequisites_inferred",
            "learning_outcomes_stated",
            "learning_outcomes_inferred",
            "contributing_authors",
        }
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    key, val = line.split(":", 1)
                    k = key.strip().lower().replace(" ", "_")
                    if k in list_keys:
                        data[k] = [v.strip() for v in val.split(",") if v.strip()]
                    else:
                        data[k] = val.strip()
            filtered = {
                k: v for k, v in data.items() if k in CourseMetadata.model_fields
            }
            if "source" not in filtered:
                filtered["source"] = path.name
            return CourseMetadata(**filtered)
        except Exception as e:
            logger.error(f"TXT parse failed: {e}")
            return CourseMetadata(source=self.course_pdf_path.name)

    def _parse_html(self, path: Path) -> CourseMetadata:
        logger.warning(f"HTML parsing not implemented for {path.name}.")
        return CourseMetadata(source=self.course_pdf_path.name)


# ── Standalone CLI ────────────────────────────────────────────────────────────
# Usage:
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --output out.json
#   python3 -m src.metadata --pdf data/courses/Dsa.pdf --output out.json --no-ai

if __name__ == "__main__":
    import argparse
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Standalone Metadata Extractor — two-phase heuristic + LLM pipeline "
            "(ADR-038). Extracts schema-complete course metadata to a reviewable "
            "JSON file."
        )
    )
    parser.add_argument(
        "--pdf", type=str, required=True, help="Path to the course PDF."
    )
    parser.add_argument(
        "--metadata",
        type=str,
        default=None,
        help="Optional explicit path or URL to an external metadata source.",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Path to save the extracted JSON metadata for review.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        default=False,
        help="Disable LLM enrichment (heuristic-only extraction).",
    )

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error(f"PDF file not found: {pdf_path}")
        sys.exit(1)

    ingestor = MetadataIngestor(
        course_pdf_path=pdf_path,
        metadata_source=args.metadata,
        use_ai=not args.no_ai,
    )
    metadata = ingestor.ingest()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(metadata.model_dump_json(indent=2))

    print(f"\nMetadata saved to: {output_path}")
    print("   Review and edit the JSON, then pass it to the evaluator:")
    print(
        f"   python3 -m src.main --input <courses_dir> --output <output_dir> "
        f"--metadata {output_path}"
    )

    print("\n── Extraction summary ──────────────────────────────────")
    fields = [
        ("title", metadata.title),
        ("author", metadata.author),
        ("publisher", metadata.publisher),
        ("level", metadata.level),
        ("target_audience", metadata.target_audience),
        (
            "prerequisites_stated",
            f"{len(metadata.prerequisites_stated)} item(s)",
        ),
        (
            "prerequisites_inferred",
            f"{len(metadata.prerequisites_inferred)} item(s)",
        ),
        (
            "learning_outcomes_stated",
            f"{len(metadata.learning_outcomes_stated)} item(s)",
        ),
        (
            "learning_outcomes_inferred",
            f"{len(metadata.learning_outcomes_inferred)} item(s)",
        ),
        ("toc", f"{len(metadata.toc)} chapter(s)"),
        ("draft_notes", metadata.draft_notes or "(none)"),
    ]
    for label, value in fields:
        status = "  " if (not value or value == "0 item(s)" or value == "(none)") else "* "
        print(f"   {status}{label:<28} {value}")
    print("────────────────────────────────────────────────────────")
