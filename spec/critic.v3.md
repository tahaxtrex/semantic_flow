# Pipeline Critic Report v3 — Cross-Validated Against Manual Assessment

**Date:** 2026-03-24  
**Source:** `PYTHON_PROGRAMMING_NOTES.pdf` (MRCET, 142 pages, 5 units)  
**Pipeline Run:** Gemini-2.5-Flash, 19 segments, 18 scored (1 dropped as exercise)  
**Validation Method:** Manual human reading of the full PDF + independent scoring on the same rubrics  
**Purpose:** Identify every pipeline component that contributed to score divergence from the manual baseline.

---

## TL;DR

The pipeline produced a Module Gate average of **6.85** vs a manual assessment of **5.2** (+1.65 inflation), and a Course Gate average of **4.4** vs manual **4.4** (coincidentally aligned, but with a catastrophic **+5 error on business_relevance**). The root causes span every pipeline stage: the segmenter created 19 fragments instead of 5 unit-aligned segments because bold subheadings trigger header detection; the classifier silently dropped 6 pages of instructional content as "exercise"; the metadata inferred the wrong target audience; the Module Gate prompt has no cross-segment awareness; and the rubric descriptions are ambiguous enough for the LLM to conflate topic coverage with pedagogical quality.

---

## Issue 1 — Bold-Font Header Detection Creates Massive Over-Segmentation (Severity: CRITICAL)

**What it looks like:**

The PDF has a body text median font size of 14.0pt (TimesNewRomanPSMT). The header threshold is `14.0 × 1.4 = 19.6pt`. No text in the body exceeds this threshold — UNIT headings are 12pt Calibri-Bold (in the page header zone), and content bold subheadings are 14pt TimesNewRomanPS-BoldMT (same size as body).

The header detection line in `_extract_blocks_with_headers()`:

```python
is_header = (len(text) < 80 and (max_size >= header_threshold or is_bold))
```

The `or is_bold` clause triggers on **497 bold lines** across the 142-page PDF. After merging, this produces **19 segments** instead of the expected **5 unit-aligned segments**.

Bold lines that incorrectly trigger header splits include:
- `"Output:"` — appears **107 times** as a bold label before code output
- `"Example:"` — 13 times
- `"Syntax:"` — 5 times
- `"Note:"` — 4 times
- Every bold subheading like `"Float:"`, `"Boolean:"`, `"Immutability:"`, `"Tuple Assignment"`, etc.

**What the segments look like:**

| Seg | Heading | Actual Content |
|-----|---------|----------------|
| 3 | `"Float:"` | Just the float/boolean/string section — one subtopic |
| 6 | `"Output:"` | A random code output line became a section boundary |
| 7 | `"Output:"` | Another output line became a boundary |
| 13 | `"Remove: The remove() method..."` | A method description became a heading |
| 15 | `"For ex: f= open (\"hello.txt\",\"w+\")"` | A code example became a heading |

**Impact on assessment:**

Each micro-segment looks locally coherent (it covers one subtopic), so the LLM scores them 7-9 on goal_focus and pedagogical_clarity. But the real quality issues — repetition across segments, lack of progressive narrative, spliced content from multiple sources — are invisible because the LLM evaluates each fragment in isolation.

**Root cause:**

The `or is_bold` fallback was added for PDFs where headers use bold-only formatting without a size increase. But for PDFs like this MRCET document where bold is used liberally for labels, output markers, and subheadings, it creates catastrophic over-segmentation.

**File:** `src/segmenter.py`, `_extract_blocks_with_headers()`, line: `is_header = (len(text) < 80 and (max_size >= header_threshold or is_bold))`

**Fix direction:**

Replace the unconditional `or is_bold` with a **running-header-aware bold filter**:

1. After the first pass collecting all bold lines, compute a frequency count. Any bold text appearing on >5% of pages (e.g., "Output:" on 107/142 pages) is a repeated label, not a structural heading — exclude it from header candidacy.
2. Bold-only headers (no size jump) should additionally require: (a) the line is fully bold (every word is bold, not just one bold word in a regular line), (b) the line does NOT end with `:` (which indicates a label, not a heading), (c) the line is not a single common word (`"Example"`, `"Syntax"`, `"Note"`, `"Output"`).
3. Add a configurable `bold_as_header: bool = True` parameter to `SmartSegmenter.__init__()` so users can disable bold-based header detection entirely for PDFs that misuse bold.

---

## Issue 2 — No Visual TOC Parser (Severity: CRITICAL)

**What it looks like:**

This PDF has no bookmark outline (Creator is empty, Producer is "convertapi.com"), so `_extract_toc()` returns `([], 142)`. But pages 4-5 contain a clearly structured visual Table of Contents:

```
UNIT  TOPIC                              PAGE NO
I     INTRODUCTION DATA, EXPRESSIONS...  1
II    CONTROL FLOW, LOOPS                35
III   FUNCTIONS, ARRAYS                  55
IV    LISTS, TUPLES, DICTIONARIES        78
V     FILES, EXCEPTIONS, MODULES...      103
```

This TOC would have given perfect 5-unit segmentation with zero ambiguity, but the pipeline cannot read visual TOCs — only PDF bookmark outlines.

**Impact on assessment:**

Without TOC-based segmentation, the pipeline falls back to the broken bold-heuristic path, producing 19 misaligned segments. Every downstream score is corrupted.

**File:** `src/segmenter.py`, `_extract_toc()`

**Fix direction:**

Add a `_extract_visual_toc()` method as a second tier in the extraction hierarchy (between bookmark TOC and font-heuristic fallback):

1. Scan pages 2-8 for lines matching `r'(UNIT|Chapter|Module|Part)\s+[\dIVXivx]+.*?(\d{1,4})\s*$'`.
2. Extract the page numbers from these lines.
3. Validate: the page numbers should be monotonically increasing and span a reasonable range of the document.
4. If ≥2 valid entries are found, use them as segment boundaries (same as the bookmark TOC path).
5. This covers the vast majority of university lecture notes, which are converted documents without bookmarks but with visual TOCs.

---

## Issue 3 — Exercise Classifier False Positive on Numbered Headings (Severity: CRITICAL)

**What it looks like:**

Segment 5, heading: `"1. Built-in functions - Functions that are built into Python."`

This heading starts with `"1."`, which matches the exercise pattern `r'^\d+[\.\)]'`. The classifier code:

```python
heading_is_exercise = any(pat.match(heading_l) for pat in _EXERCISE_PATTERNS)
if heading_is_exercise or (...):
    return "exercise"
```

The heading match alone is sufficient to classify the entire segment as `exercise`. This segment contains ~6 pages of instructional content about built-in functions, flow of execution, parameters, and arguments — all silently dropped from scoring with zero scores.

**Impact on assessment:**

6 pages of instructional content (functions, flow of execution, parameters) are excluded from the Module Gate aggregate. The weighted average loses a significant chunk of representative content, and the Course Gate receives no summary for this section, creating a gap in the course narrative.

**File:** `src/segmenter.py`, `_classify_segment()`, exercise detection block

**Fix direction:**

1. The heading-based exercise check should require the heading to ALSO contain exercise-specific keywords: `r'^\d+[\.\)]\s*(exercise|practice|problem|question|write\s+a\s+program)'`. A heading like `"1. Built-in functions"` should not trigger because the text after the number is descriptive, not imperative.
2. The body-text exercise pattern `r'\b(write a program|create a|implement|design a)\b'` should exclude matches inside `[CODE]...[/CODE]` blocks and inside comment lines (lines starting with `#`). Currently, a comment like `# Write a program to find factorial` inside a code example triggers the exercise classifier.
3. Numbered explanatory lists (e.g., `"1. The order in which statements are executed..."`) should be distinguished from numbered problems by checking whether the text after the number is a declarative statement (contains a verb in indicative mood) vs an imperative instruction (starts with an imperative verb).

---

## Issue 4 — Frontmatter Segment Scored as Instructional (Severity: HIGH)

**What it looks like:**

Segment 1 heading: `"(Autonomous Institution – UGC, Govt. of India)"`

This segment contains 3,500+ characters of:
- Institutional accreditation text ("Recognized under 2(f) and 12(B) of UGC ACT 1956...")
- Syllabus listing (UNIT I through UNIT V topic descriptions)
- Course objectives and outcomes
- Textbook references
- A table of contents with page numbers
- The beginning of the Python introduction

It is classified as `instructional` and scored with `example_concreteness: 2` and `example_coherence: 1`, dragging down the weighted Module Gate average.

**Root cause:**

The frontmatter patterns look for heading text matching `"table of contents"`, `"preface"`, etc. This segment's heading `"(Autonomous Institution – UGC, Govt. of India)"` matches none of them. The copyright detector also misses it because there are no standard copyright keywords — it's institutional accreditation boilerplate, not publisher copyright.

**File:** `src/segmenter.py`, `_classify_segment()`, frontmatter detection block

**Fix direction:**

Add institutional/syllabus boilerplate detection:

1. Add patterns to `_FRONTMATTER_PATTERNS`:
   ```python
   re.compile(r'\b(UGC|AICTE|JNTUH|affiliated\s+to|accredited\s+by|autonomous\s+institution)\b', re.IGNORECASE),
   re.compile(r'\b(syllabus|course\s+code|credit\s+hours?|L\s+T/?P/?D\s+C)\b', re.IGNORECASE),
   ```
2. Add a content-based heuristic: if a segment contains both `"UNIT I"` through `"UNIT V"` markers AND textbook references (`"Text Books"` or `"Reference Books"`), classify as `frontmatter`.
3. Check the first 500 characters of the segment for institutional markers before falling through to `instructional`.

---

## Issue 5 — Metadata Infers Wrong Target Audience (Severity: HIGH)

**What it looks like:**

Extracted metadata:
```json
{
  "target_audience": "Introductory college students or beginners with no prior background",
  "level": "Introductory"
}
```

The actual PDF explicitly states: **"B.TECH III YEAR – II SEM"** and **"III Year B. Tech CSE -II SEM"** — these are third-year Computer Science engineering students, not beginners.

**Root cause:**

The metadata pipeline detected the word "introduction" in the text (from "Introduction to Python and installation"), mapped it to `level: "Introductory"`, then used the generic level-audience map:

```python
level_audience_map = {
    "Introductory": "Introductory college students or beginners with no prior background",
}
```

This overrode the actual explicit audience stated in the PDF.

**Impact on assessment:**

Every evaluation prompt receives the wrong audience context. The LLM grades content as if it's for absolute beginners (which inflates scores — trivial `a=5` examples seem appropriate for beginners). With the correct audience ("III Year B.Tech CSE students"), the LLM would penalize the lack of advanced material and the mismatch between student level and content depth.

**File:** `src/metadata.py`, `_infer_from_text()`, target audience and level inference blocks

**Fix direction:**

1. Before inferring level from generic keywords, scan for explicit academic year/semester markers: `r'(\d+(?:st|nd|rd|th)\s+year|[IVX]+\s+year|year\s+[IVX\d]+|B\.?Tech|M\.?Tech|semester\s+[IVX\d]+)'`. If found, extract the year/semester and use it as the audience directly (e.g., `"III Year B.Tech CSE students"`).
2. The level inference should not trigger on "introduction" when it appears as a topic name (`"Introduction to Python"`) rather than a course-level descriptor. Check context: if "introduction" is followed by "to [Subject]", it describes the subject, not the course level.
3. Add explicit audience patterns for university lecture notes: `r'(\d+(?:st|nd|rd|th)?\s+(?:year|sem|semester)[^.\n]{0,80})'`.

---

## Issue 6 — Business Relevance Rubric is Ambiguous (Severity: HIGH)

**What it looks like:**

Pipeline score: `business_relevance: 8`. Manual score: `3`. Delta: **+5**.

The LLM rationale: *"The course directly addresses the stated learning outcomes, which are highly practical for anyone learning Python."*

The LLM equated "topics match the learning outcomes" with "content is practically applicable in real-world settings." The actual content has zero industry context, zero real-world projects, zero case studies, and no discussion of how Python is used professionally. For III Year B.Tech students approaching industry, this is a significant gap.

**Root cause:**

The rubric description says:
> "Are specific, practical takeaways immediately applicable to the stated Learning Outcomes from the Course Metadata?"

This wording lets the LLM pattern-match: learning outcomes mention file I/O → content covers file I/O → "immediately applicable" → score 8. The rubric does not distinguish between syntactic coverage and applied competency.

**File:** `config/rubrics.yaml`, `business_relevance` definition

**Fix direction:**

Rewrite the rubric description to be unambiguous:

```yaml
- id: business_relevance
  name: "Business Relevance"
  description: >
    Does the course connect its content to real-world professional tasks that the
    Target Audience would face in their careers? Are there projects, case studies,
    industry scenarios, or applied exercises that demonstrate HOW and WHERE to use
    the skills being taught? Simply covering a topic that appears in the Learning
    Outcomes is NOT sufficient — the content must show practical application context.
    A course that teaches syntax without showing real-world usage scores low.
  scoring_guide:
    low: "1-3: Purely syntactic/theoretical; no real-world scenarios, projects, or industry context"
    mid: "4-6: Some practical elements but examples use trivial/generic data (a=5, x=[1,2,3])"
    high: "7-10: Rich real-world scenarios, industry-relevant projects, applied case studies throughout"
```

---

## Issue 7 — Example Concreteness Rubric Conflates Code Presence with Real-World Relevance (Severity: HIGH)

**What it looks like:**

Pipeline `example_concreteness` average: **8.15**. Manual score: **6**. Delta: **+2.15**.

The LLM consistently scores 9/10 on segments containing code examples like:
```python
a = 5
b = 2
print(a/b)
```

This IS concrete (it's real runnable code) but is NOT relatable or a "plausible representation of a real-world situation" as the rubric requires.

**Root cause:**

The rubric description says: *"Are examples concrete, relatable, and plausible representations of real-world situations?"*

The scoring guide anchors are:
- low: "Abstract, contrived, or missing examples"
- high: "Rich, concrete, real-world examples throughout"

The LLM interprets "concrete" as "actual code that runs" rather than "code that solves a recognizable real-world problem." `a=5; b=2; print(a/b)` is concrete but not relatable.

**File:** `config/rubrics.yaml`, `example_concreteness` definition

**Fix direction:**

Sharpen the scoring guide anchors:

```yaml
scoring_guide:
  low: "1-3: No examples, or only abstract pseudo-descriptions without actual code/scenarios"
  mid: "4-6: Code examples present but use trivial/generic data (a=5, x=[1,2,3], 'mrcet college'); no real-world motivation for the examples"
  high: "7-10: Examples use realistic scenarios (student records, inventory systems, file processing, data analysis) that demonstrate WHY a concept is useful, not just HOW to type it"
```

---

## Issue 8 — Module Gate Has No Cross-Segment Awareness (Severity: HIGH)

**What it looks like:**

Segments 9 and 10 repeat the same topics (function composition, recursion, string slicing) — the LLM even notes this in its rationale for segment 10: *"significant overlap and repetition of topics from Segment 9."* But it still scores `example_concreteness: 9` because each segment individually contains code examples.

The `"(or)"` pattern — where two alternative definitions from different sources are pasted back-to-back — appears in multiple segments but is never penalized as a coherence issue because each segment is evaluated in isolation.

The string `"mrcet college"` appears as a variable value in examples across segments 3, 7, 8, 10, 11, 12, 13, 14 — institutional branding masquerading as pedagogical examples — but no single segment evaluation catches this as a cross-segment coherence problem.

**Root cause:**

The Module Gate batch prompt sends segments without any context about what came before. Each batch of 5 segments is evaluated as if it were the only content in the course. The LLM cannot detect:
- Topic repetition across segments
- Progressive (or non-progressive) example complexity
- Recurring placeholder values used across the course
- Content spliced from multiple uncited sources

**File:** `src/evaluator.py`, `_build_module_batch_prompts()`

**Fix direction:**

Add cross-segment context to the Module Gate prompt:

1. For each segment after the first, inject the previous segment's summary: `"Previous segment covered: {prev_summary}"`. This enables the LLM to detect repetition and assess continuity.
2. For each batch after the first, include a brief "course narrative so far" comprising all previous segment summaries (truncated to ~500 chars total). This gives the LLM a sense of where the course has been.
3. Add explicit prompt instructions: *"Penalize example_coherence if examples use the same trivial variables (a, b, x) or institutional names ('mrcet') repeatedly without building toward a progressive learning narrative. Penalize pedagogical_clarity if the segment repeats topics already covered in earlier segments (see 'Previous segment covered' above)."*

---

## Issue 9 — Course Gate Receives Only Summaries, Not Quality Signals (Severity: HIGH)

**What it looks like:**

The Course Gate prompt receives:
1. Course metadata
2. Non-instructional segment text (truncated to 1500 chars)
3. A bulleted list of 1-2 sentence summaries per segment

From these summaries, the course LOOKS comprehensive: "covers data types, control flow, functions, data structures, files/exceptions, modules/packages." The summaries do not convey:
- That examples use trivial `a=5, b=2` variables throughout
- That content is spliced from multiple sources with inconsistent style
- That there are zero exercises or self-check questions
- That the `"(or)"` pattern signals unharmonized source material
- That every string example uses "mrcet college"

This is why `business_relevance` scored 8 — the summaries describe topic coverage, not content quality.

**File:** `src/evaluator.py`, `_build_course_prompts()`

**Fix direction:**

Enrich the Course Gate input with quality signals from the Module Gate:

1. For each segment summary, append the Module Gate scores: `"[Scores: GF=8, TR=7, PC=7, EC=7, EH=4]"`. This lets the Course Gate see which segments scored poorly and factor that into holistic assessment.
2. Include a "Module Gate Quality Summary" section in the Course Gate prompt:
   ```
   MODULE GATE QUALITY SUMMARY:
   - Average example_coherence across segments: 5.37 (below midpoint — examples are scattered)
   - Lowest-scoring segment: Seg 12 (pedagogical_clarity: 2 — confusing array/list transition)
   - Segments with repetition detected: Seg 9 and Seg 10 overlap on function composition/recursion
   ```
3. Add an explicit prompt instruction: *"Do NOT score business_relevance based solely on whether topics match learning outcomes. Assess whether the actual content delivery — as reflected in the module scores and summaries — creates applicable professional skills."*

---

## Issue 10 — Dual CourseMetadata Class Definition (Severity: MEDIUM)

**What it looks like:**

`src/models.py` defines:
```python
class CourseMetadata(BaseModel):
    title: Optional[str] = None  # defaults to None
```

`src/metadata.py` defines:
```python
class CourseMetadata(BaseModel):
    title: str = "Unknown"  # defaults to "Unknown"
```

`MetadataIngestor` returns `metadata.py`'s `CourseMetadata`. The evaluator imports `models.py`'s `CourseMetadata`. When `metadata.title` is `"Unknown"` (from metadata.py), the evaluator prompt renders it as the string `"Unknown"` rather than omitting it. But if metadata.py returned `None`, Pydantic would render `"None"` in the f-string.

**Impact on assessment:**

The metadata object type flows through `main.py` → `evaluator.py` → `aggregator.py`. Because Pydantic will serialize either class's instances to compatible dicts, no crash occurs. But the semantic mismatch means field-presence checks like `metadata.title or 'Unknown'` behave differently depending on which class produced the object (`None` is falsy, `"Unknown"` is truthy).

**File:** `src/models.py` and `src/metadata.py`

**Fix direction:**

Delete `CourseMetadata` from `src/models.py` and import it from `src/metadata.py` everywhere. There should be a single source of truth for this schema. The `"Unknown"` sentinel convention from `metadata.py` is better because it avoids `None`-handling in f-strings.

---

## Issue 11 — Rubrics YAML Contains Invalid `1.0j` Weight (Severity: MEDIUM)

**What it looks like:**

In `config/rubrics.yaml`, the `instructional_alignment` rubric:
```yaml
weight: 1.0j
```

`1.0j` is a Python complex number literal. YAML parsers will read this as the string `"1.0j"` (not a number). If the pipeline ever numerically parses rubric weights (e.g., for weighted scoring), this will cause a crash or silent `0` weight.

**File:** `config/rubrics.yaml`, `instructional_alignment` weight field

**Fix direction:**

Change `1.0j` to `1.0`.

---

## Issue 12 — Merge Logic Has No Unit Boundary Awareness (Severity: MEDIUM)

**What it looks like:**

The `_merge_short_blocks()` method prevents merging across headings matching `r'^(chapter|module|unit|part)\s+\d+'`. But when the segmenter over-segments on bold lines, most headings are things like `"Output:"`, `"Float:"`, `"Syntax:"` — none of which match the chapter-start pattern.

This means the merge step can combine the end of Unit I with the start of Unit II if they are adjacent short blocks with non-matching headings. The logical unit boundary is destroyed.

**File:** `src/segmenter.py`, `_merge_short_blocks()`

**Fix direction:**

1. Before merging, scan all block headings for unit/chapter markers (including those in the text body, not just the heading): `r'UNIT\s+[–-]?\s*[IVX\d]+'`. Mark these blocks as merge barriers.
2. Also detect page-number discontinuities: if the visual TOC parser (Issue 2) provides page boundaries, use them as merge barriers even when the heading doesn't match.

---

## Issue 13 — Score Distribution Shows Positive Skew / Leniency Bias (Severity: MEDIUM)

**What it looks like:**

Across all 90 dimension-scores (18 segments × 5 dimensions):
- 68% of scores are ≥7
- Mode is 8 (27 occurrences) and 9 (24 occurrences)
- Only 18% are ≤4
- No scores of 10 at all (ceiling compression)
- Mean: 6.94

This distribution shows strong leniency bias: the LLM defaults to "good enough" (7-9) for any segment that contains relevant content, regardless of quality nuance.

**Root cause:**

The scoring procedure prompt says: *"identify whether evidence points to above midpoint (>5) or below (<5)."* This binary anchor pushes scores toward 7-8 (just above midpoint) rather than spreading across the full range. The LLM rarely has strong enough evidence to go below 5 because each segment IS about the right topic.

**File:** `src/evaluator.py`, `_build_module_batch_prompts()`, SCORING PROCEDURE section

**Fix direction:**

Replace the binary above/below-midpoint anchor with a **three-step calibration procedure**:

```
SCORING PROCEDURE (apply for every rubric, every segment):
1. IDENTIFY: Find 2-3 specific pieces of evidence from the segment text relevant to this rubric.
2. ANCHOR: Compare the evidence to the scoring guide anchors. Which band does it fall in?
   - 1-3 (poor): Evidence shows fundamental problems (missing content, broken structure, incorrect information)
   - 4-6 (adequate): Content is present and functional but has clear weaknesses
   - 7-8 (good): Content is well-executed with minor issues
   - 9-10 (excellent): Content is exemplary and could serve as a model for others
3. DIFFERENTIATE: Within the chosen band, assign the specific score. A 7 means "good with some rough edges"; an 8 means "good with only minor issues." Do not default to 8 — justify why it is not 7 or 9.

CALIBRATION ANCHORS (use these to prevent score inflation):
- A segment with code examples using only trivial variables (a, b, x, y) and institutional names should NOT score above 6 on example_concreteness.
- A segment that repeats content from earlier segments should NOT score above 5 on example_coherence.
- A segment where technical terms are used before being defined should NOT score above 5 on pedagogical_clarity.
```

---

## Issue 14 — `_extract_blocks_with_headers` Running Header Detection is Position-Only (Severity: LOW-MEDIUM)

**What it looks like:**

The body crop `(0, H*0.10, W, H*0.92)` successfully removes the top-of-page running headers for this PDF (they're at y=38.4, within the top 4.8%). But for PDFs where running headers sit at 11-12% from the top (e.g., textbooks with tall header bars), the 10% crop won't catch them.

More importantly, there is no **content-based** running header detection. If the same text appears on every page at the same Y-coordinate within the body zone, it should be stripped regardless of its position.

**File:** `src/segmenter.py`, `_extract_blocks_with_headers()`, body crop parameters

**Fix direction:**

Add a post-extraction running header filter:

1. After collecting all lines from all pages, compute a frequency table of `(text, approximate_y_position)` pairs.
2. Any text that appears on >30% of pages at a similar Y-position (within 5pt) is a running header/footer — strip it from all pages.
3. This handles non-standard header positions, watermarks, and institutional branding that appears within the body region.

---

## Issue 15 — Evaluator LLM Docstring Says "6 rubrics" but Module Gate Has 5 (Severity: LOW)

**What it looks like:**

The `LLMEvaluator` class docstring says:
> "Gate 1 — Module Gate: Evaluates each instructional segment in batches on 6 rubrics (readability, clarity, examples, goal focus, instructional alignment)."

But after ADR-016/ADR-028, `instructional_alignment` was moved to the Course Gate. The Module Gate now has 5 rubrics: `goal_focus`, `text_readability`, `pedagogical_clarity`, `example_concreteness`, `example_coherence`.

Similarly, the Course Gate docstring says "4 holistic rubrics" but it now has 5 (including `instructional_alignment`).

**File:** `src/evaluator.py`, class docstring

**Fix direction:**

Update the docstring to reflect the current 5+5 split.

---

## Issue 16 — Course Gate Prompt Says "4 Course Gate rubrics" (Severity: LOW)

**What it looks like:**

The Course Gate user prompt ends with:
```python
user_prompt += "\n\nNow evaluate the course holistically on the 4 Course Gate rubrics."
```

But the Course Gate now has 5 rubrics (after `instructional_alignment` was added). The LLM may only produce 4 scores if it takes this instruction literally.

**File:** `src/evaluator.py`, `_build_course_prompts()`, final line of user_prompt

**Fix direction:**

Change `"the 4 Course Gate rubrics"` to `"the 5 Course Gate rubrics"` or simply `"the Course Gate rubrics listed above"`.

---

## Summary Table

| # | Issue | Component | Severity | Impact on Scores |
|---|-------|-----------|----------|------------------|
| 1 | Bold-font header over-segmentation | segmenter | **CRITICAL** | 19 fragments vs 5 units; all dimensions affected |
| 2 | No visual TOC parser | segmenter | **CRITICAL** | Falls back to broken heuristic path |
| 3 | Exercise classifier false positive on numbered headings | segmenter | **CRITICAL** | 6 pages of instructional content silently dropped |
| 4 | Frontmatter segment scored as instructional | segmenter | **HIGH** | Syllabus/accreditation content drags down Module Gate average |
| 5 | Metadata infers wrong target audience | metadata | **HIGH** | All prompts grade against wrong audience baseline |
| 6 | Business relevance rubric is ambiguous | rubrics.yaml | **HIGH** | +5 score inflation (8 vs 3) |
| 7 | Example concreteness conflates code with real-world | rubrics.yaml | **HIGH** | +2.15 average inflation |
| 8 | Module Gate has no cross-segment awareness | evaluator | **HIGH** | Repetition/coherence issues invisible |
| 9 | Course Gate receives only summaries, no quality signals | evaluator | **HIGH** | Holistic assessment based on topics, not quality |
| 10 | Dual CourseMetadata class definition | models/metadata | **MEDIUM** | Semantic mismatch in field defaults |
| 11 | Rubrics YAML `1.0j` typo | rubrics.yaml | **MEDIUM** | Potential crash on weight parsing |
| 12 | Merge logic has no unit boundary awareness | segmenter | **MEDIUM** | Cross-unit merging destroys structure |
| 13 | LLM leniency bias (68% scores ≥7) | evaluator | **MEDIUM** | Systematic +1.65 Module Gate inflation |
| 14 | Running header detection is position-only | segmenter | **LOW-MED** | Fails on non-standard header positions |
| 15 | Evaluator docstring says "6 rubrics" (now 5) | evaluator | **LOW** | Documentation mismatch |
| 16 | Course Gate prompt says "4 rubrics" (now 5) | evaluator | **LOW** | LLM may produce only 4 scores |

---

## Recommended Fix Priority (Ordered by Score Accuracy Impact)

1. **Fix bold header detection** (Issue 1) + **Add visual TOC parser** (Issue 2) — These two fixes together would transform segmentation from 19 broken fragments to 5 clean unit-aligned segments. Every downstream score improves. **Estimated impact: ±1.5 points on Module Gate.**

2. **Fix exercise classifier** (Issue 3) + **Fix frontmatter detection** (Issue 4) — Prevents 6+ pages of instructional content from being silently dropped and stops syllabus pages from corrupting the Module Gate average. **Estimated impact: ±0.5 points on Module Gate.**

3. **Sharpen rubric descriptions** (Issues 6, 7) — Rewording business_relevance and example_concreteness to include explicit anti-patterns prevents the LLM from conflating topic coverage with quality. **Estimated impact: ±2 points on business_relevance, ±1 point on example_concreteness.**

4. **Fix metadata audience inference** (Issue 5) — Correct audience context changes the grading baseline for every prompt. **Estimated impact: ±0.5 points across all dimensions.**

5. **Add cross-segment context to Module Gate** (Issue 8) + **Enrich Course Gate input** (Issue 9) — Enables detection of repetition, inconsistent style, and progressive (or non-progressive) quality. **Estimated impact: ±1 point on example_coherence and fluidity_continuity.**

6. **Fix leniency bias in scoring procedure** (Issue 13) — Better calibration anchors spread scores across the full range instead of clustering at 7-9. **Estimated impact: ±0.5 points across all dimensions.**

7. **Fix remaining issues** (Issues 10-12, 14-16) — Housekeeping fixes that prevent edge-case crashes and documentation drift. **Low score impact but improves robustness.**