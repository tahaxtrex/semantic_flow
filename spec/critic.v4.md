# Pipeline Critic Report v4 — Structural Issues from Evaluation Audit

**Date:** 2026-04-15
**Source:** Post-evaluation audit across multiple course PDFs
**Purpose:** Document four structural problems identified from live evaluation results, assess the current state of the codebase against each, and specify what must change.

---

## TL;DR

Four problems surfaced from evaluation output analysis. One is a scoring-prompt design flaw (double-penalty for a single segmentation artifact). One is a segmentation classification threshold issue (over-tagging prose as reference_table, fragile glossary/preface detection). One is a rubric calibration failure (LLM positivity leakage, `business_relevance` ceiling effect). One is an architectural validation gap (Course Gate is implemented but its input quality and output reliability have not been tested). None of these were introduced by recent changes — all four predate ADR-041/042/043 — but the criteria-based scoring (ADR-043) partially addresses Problem 3.

---

## Issue 1 — Heading Mismatch Creates Double Score Penalty (Severity: HIGH)

**Status: Not fixed**

### What it looks like

When the segmenter assigns a heading that does not match the segment's body content — for example, because a page boundary falls mid-chapter and the chapter title from the previous page ends up as the heading for a trailing-content segment — the LLM penalizes the same underlying flaw twice:

- **Module Gate**: `goal_focus` criterion C4 — "The opening of the segment makes the learning intent clear" — scores 0 or 1 because the heading says "Chapter 3: Virtual Memory" but the body starts with the last paragraphs of Chapter 2.
- **Course Gate**: `fluidity_continuity` criterion 4 — "No module feels like it was inserted out of place" — scores 0 or 1 because, from the distilled summaries, the module titled "Virtual Memory" appears to discuss processes, making the course look structurally incoherent.

One segmentation artifact produces two independent score penalties across two gates. The aggregate score is distorted by a pipeline error, not a course quality issue.

### Current code state

The Module Gate system prompt in `_build_module_batch_prompts()` (`src/evaluator.py`) has an EXTRACTION NOTES block that exempts figure references, `[TABLE:]` markers, `[CODE]` blocks, and CID artifacts. There is no equivalent exemption for heading/body disagreements.

Tier 0 segmentation (ADR-042, `_extract_toc_metadata()`) reduces the incidence of heading mismatches by finding chapter-start pages by title match, but does not eliminate them. Pages very close to a chapter boundary can still have ambiguous text that lands in the wrong block. For PDFs that fall through to Tier 3 (font heuristic), heading mismatches remain frequent.

`goal_focus` criterion C4 as currently written evaluates the heading signal unconditionally. The Course Gate prompt has no instruction protecting `fluidity_continuity` from segmentation artifacts.

### What needs to change

**`src/evaluator.py` — `_build_module_batch_prompts()`:**

Extend the EXTRACTION NOTES block with:

> "If a segment's extracted heading appears inconsistent with its body text (e.g. the heading names a chapter whose content does not appear until the next segment), treat this as a PDF extraction boundary artifact. Evaluate the body content on its own merits. Do NOT penalize `goal_focus` solely because the extracted heading and the body text are misaligned — only penalize when the body itself is unfocused or digressive."

**`src/evaluator.py` — `_build_course_prompts()`:**

Add to the system prompt:

> "NOTE: Apparent module-ordering anomalies (a module whose summary discusses a different topic than its heading implies) are typically PDF segmentation artifacts, not course design flaws. Do not penalize `fluidity_continuity` for heading/content mismatches."

**`config/rubrics.yaml` — `goal_focus` criterion C4:**

Change from:
> "The opening of the segment makes the learning intent clear"

To:
> "The body of the segment delivers coherently on the topic it actually teaches — evaluated on content structure, not on whether the extracted heading label matches"

---

## Issue 2 — reference_table Over-Tagging, Weak Preface/Glossary Detection (Severity: MEDIUM)

**Status: Partially fixed (ADR-040), threshold still loose**

### What it looks like

Two related failures:

**A — reference_table over-tagging:** Segments with substantial instructional prose (40%+ plain text) are being classified as `reference_table` and silently bypassed by the Module Gate. An SQL chapter with five syntax tables, a data-types chapter with a comparison grid, or a configuration chapter with example listings all share the structural fingerprint of `[TABLE:]` count ≥ 4 and prose density ≤ 0.60 — the current gate threshold.

**B — Preface/glossary misclassification:** Preface pages that arrive with the heading `"Frontmatter"` (assigned by Tier 0) are not caught by `_PREFACE_HEADING_RE`, which only matches exact strings `"preface"`, `"foreword"`, `"introduction"`, `"about this book"`. They fall through to `_is_copyright_page()` and then default to `"instructional"`, losing the Course Gate structural context signal. Glossary blocks at the end of chapters with non-standard headings (e.g. `"Definitions"`, `"Terminology"`, `"Key Vocabulary"`) are not caught by `_GLOSSARY_HEADING_PATTERNS` (which only matches `"Key Terms"` and `"Glossary"`), and the body-content fallback only checks the first 200 characters.

### Current code state

**`src/segmenter.py` — `_classify_segment()`, Path 2:**

```python
# Line 1171
if text.count("[TABLE:") >= 4 and prose_density <= 0.60:
    return "reference_table"
```

The `0.60` threshold was set in ADR-040 to prevent instructional chapters with a few tables from being bypassed. But `≤ 0.60` still catches segments that are 40% plain prose — too aggressive for instructional content.

**Glossary body fallback:**

```python
# Line 1212
if re.search(r'(^|\n)key\s+terms(\n|$)', text[:200], re.IGNORECASE):
    return "glossary"
```

Checks only the first 200 characters for the exact phrase "key terms". A glossary section that starts two paragraphs into the segment, or uses a different label, is never caught.

**Preface:** `_PREFACE_HEADING_RE` requires the heading to be one of four specific strings as an exact line match. The `"Frontmatter"` heading produced by Tier 0 does not match.

### What needs to change

**`src/segmenter.py` — Path 2 threshold:**

Raise prose density threshold from `0.60` to `0.75`. Lower the `[TABLE:]` count gate from `≥ 4` to `≥ 2` to compensate — the combination of more tables AND denser non-prose content is the real signal for a reference table.

```python
if text.count("[TABLE:") >= 2 and prose_density <= 0.75:
    return "reference_table"
```

**`src/segmenter.py` — Glossary body detection:**

Replace the 200-character probe with a pattern that scans the full segment for definition-list structure:

```python
# Count lines that look like glossary definitions: "Term: definition" or "Term — definition"
definition_lines = sum(
    1 for ln in text.splitlines()
    if re.match(r'^\s*\*{0,2}[A-Z][^:—\n]{2,40}[:\—–]\s+\S', ln)
)
if definition_lines >= 5:
    return "glossary"
```

Also extend `_GLOSSARY_HEADING_PATTERNS` to include `"definitions"`, `"terminology"`, and `"key vocabulary"`.

**`src/segmenter.py` — Preface heading match:**

In `_classify_segment()`, add a check for `"Frontmatter"` headings that appear before the first chapter:

```python
if heading_l in ("frontmatter",) and self._is_before_first_chapter(block_index):
    # Check if the body reads like a preface (course/book introduction prose)
    preface_signals = sum(1 for phrase in (
        "this book", "this course", "the reader", "chapter by chapter",
        "how to use", "who should read", "what you will learn"
    ) if phrase in text.lower())
    if preface_signals >= 2:
        return "preface"
```

---

## Issue 3 — LLM Positivity Leakage, business_relevance Ceiling Effect (Severity: HIGH)

**Status: Partially addressed by ADR-043 criteria scoring, rubric still needs tightening**

### What it looks like

Module Gate scores cluster in the 6–8 range regardless of actual content quality. `business_relevance` almost always reaches 9–10 in the Course Gate even for courses that demonstrate syntax only and never show a professional workflow. Both effects are caused by the same root: rubric descriptions and criteria that are easy to satisfy at a surface level, with no explicit anti-inflation protection.

### Current code state

**Criteria-based scoring (ADR-043)** partially mitigates this. Instead of one holistic 1-10 estimate, the LLM must score 5 specific criteria. This makes it harder to anchor high without evidence. However, the `business_relevance` criteria as currently written remain structurally lenient:

- C2: "At least some examples or exercises use industry-relevant scenarios" — "at least some" is trivially satisfied by one vaguely domain-labelled example in the entire course.
- C5: "A working professional in the target role would find the content practically useful" — this is a sentiment question the LLM answers with optimistic extrapolation.

The `business_relevance` description in `config/rubrics.yaml` has a scoring guide that warns against surface-level coverage, but no `WARNING — Anti-inflation rule:` block of the kind present in `instructional_alignment`. This is the primary reason inflation continues: the LLM sees no explicit instruction that topic presence ≠ business relevance.

The Module Gate system prompt (`_build_module_batch_prompts()`) has calibration anchors for `goal_focus`, `text_readability`, `pedagogical_clarity`, `example_concreteness`, and `example_coherence` — but no anchor for any of these at the course level, and no Module Gate-equivalent calibration for `business_relevance`.

### What needs to change

**`config/rubrics.yaml` — `business_relevance` description:**

Add an anti-inflation rule identical in structure to the one on `instructional_alignment`:

> "WARNING — Anti-inflation rule: The presence of a domain-labelled example (e.g. a variable named `salary`) does NOT constitute business relevance. A course that names a real-world domain but never shows a professional making a decision, solving a task, or using a skill in a realistic workflow anchors at 4 (mid). A score of 7+ requires demonstrated professional workflow context — a realistic scenario, a real task, or an explicit bridge from the concept to professional use. A 9–10 requires this consistently across modules."

**`config/rubrics.yaml` — `business_relevance` criteria C2:**

Change from:
> "At least some examples or exercises use industry-relevant scenarios"

To:
> "The majority of examples use industry-relevant scenarios with realistic data — not domain-labelled placeholders"

**`src/evaluator.py` — `_build_module_batch_prompts()`:**

Add a `business_relevance` calibration anchor to the CALIBRATION ANCHORS section:

```
  business_relevance:
    2 — No real-world context at all; every example uses a=5, x=[1,2,3], or equivalent
    5 — Domain label present (e.g. "employee salary") but no professional workflow or decision shown
    8 — Realistic scenario with professional task: "calculate overtime pay given shift data from a CSV"
```

**`src/evaluator.py` — Course Gate prompt:**

Add a `business_relevance` note to the course-level scoring procedure:

> "For business_relevance: score LOW (0-1 per criterion) if the course only labels examples with domain names without showing professional decision-making. Domain-labelled placeholders (employee_salary = 50000) are NOT industry context."

---

## Issue 4 — Course Gate Not Validated End-to-End (Severity: MEDIUM)

**Status: Implemented, not validated**

### What it looks like

ADR-016 defined the two-gate architecture. The Course Gate code exists and runs — `_call_claude_course()`, `_call_gemini_course()`, `_build_course_prompts()`, and the `_COURSE_EVAL_TOOL` schema are all implemented. But the quality of the input the Course Gate receives has not been verified to be sufficient for reliable assessment of each rubric, and there are no tests for the Course Gate output.

### Current code state

**Input quality gaps identified:**

1. **Non-instructional segment truncation (`_build_course_prompts()`, line ~615):**
   ```python
   if len(text) > 1500:
       text = text[:1500] + "\n[... truncated for brevity ...]"
   ```
   The TOC — the primary evidence for `structural_usability` — is truncated at 1500 characters. A course with 20+ chapters has its TOC cut before the last 10-15 entries. The rubric asks "Is there a clear table of contents that reflects the actual module structure?" but the LLM only sees half of it.

2. **Module summary compression:** The Module Gate is asked for a "1-2 sentence summary" per segment. These summaries are the primary input to `fluidity_continuity`, `prerequisite_alignment`, and `instructional_alignment`. Two sentences cannot reliably encode which prerequisites a module assumes or how it connects to the next module.

3. **`_detect_partial_course()` brittleness:** The heuristic checks for `"chapter 1"`, `"module 1"`, `"unit 1"`, or `"introduction"` in headings and text. PDFs whose first chapter is not labelled with "1" (e.g. chapter zero, roman numeral I, or "Fundamentals") return `True` (partial course), injecting a misleading disclaimer that suppresses legitimate penalization.

4. **No test coverage:** There are no tests for `evaluate_course()`, `_build_course_prompts()`, `_detect_partial_course()`, or the two course call functions. The only validation is manual inspection of the output JSON.

### What needs to change

**`src/evaluator.py` — non-instructional segment truncation:**

Raise the truncation limit for non-instructional segments from 1500 to 4000 characters specifically for segments classified as `frontmatter` or `preface` (where the TOC and course overview live). Keep 1500 for other non-instructional types (glossary, exercise, etc.):

```python
truncation_limit = 4000 if seg.segment_type in ("frontmatter", "preface") else 1500
if len(text) > truncation_limit:
    text = text[:truncation_limit] + "\n[... truncated for brevity ...]"
```

**`src/evaluator.py` — Module Gate summary instruction:**

In `_build_module_batch_prompts()`, change the summary instruction from:

> "1-2 sentence content summary of this segment's topic and key concepts"

To:

> "3-4 sentence summary covering: (1) the topic and key concepts taught, (2) the prerequisite knowledge this segment assumes, (3) what the learner can do after reading it, and (4) how it connects to the next topic"

**`src/evaluator.py` — `_detect_partial_course()`:**

Add a fallback heuristic: if the PDF has fewer than 30 total segments and no TOC signal in any non-instructional segment, treat as partial. Also extend the `_FIRST_CHAPTER_RE` pattern to match chapter-zero and roman-numeral headings:

```python
_FIRST_CHAPTER_RE = re.compile(
    r'^(chapter\s*(0|1|i|one)\b|module\s*(0|1|i|one)\b|unit\s*(0|1|i|one)\b|introduction\b|foundations?\b|fundamentals?\b)',
    re.IGNORECASE,
)
```

**`tests/` — Course Gate integration test:**

Add `tests/test_course_gate.py` with at minimum:
- A fixture `CourseEvaluation` built from canned segment data.
- Assert `criteria_scores` keys in `CourseAssessment` match the 5 course rubric IDs.
- Assert `overall_score` is the mean of `scores` values.
- Assert `_detect_partial_course()` returns `False` for a fixture with a "Chapter 1" heading and `True` for a fixture with no chapter-like headings.

---

## Summary Table

| Issue | Severity | Current state | Fix scope |
|-------|----------|---------------|-----------|
| 1 — Heading mismatch double-penalty | HIGH | Not fixed — no prompt guard exists | `evaluator.py` prompt (2 locations), `rubrics.yaml` C4 wording |
| 2 — reference_table over-tagging, weak preface/glossary | MEDIUM | Partially fixed by ADR-040, threshold too loose | `segmenter.py` threshold + 3 detection improvements |
| 3 — Positivity leakage, business_relevance ceiling | HIGH | Partially fixed by ADR-043 criteria scoring | `rubrics.yaml` anti-inflation rule + C2 rewording, `evaluator.py` calibration anchor |
| 4 — Course Gate not validated | MEDIUM | Code exists, no tests, input quality gaps | `evaluator.py` truncation + summary length, `_detect_partial_course()`, new test file |
