# Extraction & Pipeline Critic Report

**Date:** 2026-02-22
**Source:** `data/output/Python Programming Fundamentals (2010)_evaluation.json`
**Run:** 14 segments evaluated by Claude 4.6 Sonnet
**Purpose:** Identify all artifacts introduced by the extraction/segmentation pipeline that corrupt the LLM's assessment — independent of actual course quality.

---

## TL;DR

The evaluation is severely compromised before the LLM sees a single word. At least **8 distinct extraction artifacts** are present in the text fed to Claude. The most damaging is word-concatenation (e.g., `OpenuptheWingIDEoracommandprompt`), which forces Claude to penalise `text_readability` at 1/10 on segments that are clearly readable in the original PDF. Because the same artifact is inconsistently present (some lines fine, some garbled), the LLM's scores fluctuate wildly across segments — not because the course quality changes, but because the extraction quality changes.

---

## Issue 1 — Word Concatenation (Severity: CRITICAL)

**What it looks like:**
```
programminglanguage.Learningtoprogramisabitlikelearningtoplaypiano
Youcanpressandholdthecontrolkey(i.e.thectrlkey)andpress'd'toexitPython
ASCIIcharacterequivalentyouusethechr conversionoperator
```

**Affected segments:** 1 (partial), 2, 3, 5, 6, 7, 8, 9, 11, 12, 13, 14 — essentially every segment.

**What Claude said:**
- Seg 2: `"text_readability": 1` — *"The text suffers from severe formatting issues, primarily the complete lack of spaces between words... makes the content almost unreadable"*
- Seg 7: `"text_readability": 1` — *"lack of spaces between words (e.g., 'xandymaybefloatsorints')"*
- Seg 4: `"text_readability": 8` — same PDF, same problem, but the particular lines fed to Claude happened to have more normal spacing.

**Root cause:**
`extract_text_lines()` reconstructs text from character bounding boxes. When a PDF uses ligature-encoded fonts or non-standard character spacing (common in older academic PDFs and Springer textbooks), pdfplumber cannot reliably reconstruct word boundaries from the glyph stream. The spaces between words exist visually in the PDF but are encoded as kerning, not as space characters — so they are dropped.

**Impact on assessment:**
`text_readability` is systematically underscored across the entire run. Segments that happen to hit a well-encoded page score 6–8; segments that hit a ligature-heavy page score 1–2. The variance is an artifact of page layout, not course quality.

**Fix direction:**
Switch from `extract_text_lines()` to `page.extract_text(use_text_flow=True)` or use `extract_words()` with a tolerance parameter to reconstruct word boundaries from word-level bounding boxes rather than character-level. Alternatively, use `pdfplumber`'s `tolerance` and `x_tolerance` parameters to detect inter-word gaps.

---

## Issue 2 — Table Headers Misidentified as Section Headings (Severity: HIGH)

**What it looks like:**

| Segment | Heading |
|---|---|
| 4 | `"1 Binary Dec Char Binary Dec Char Binary Dec Char"` |
| 5 | `"1 Binary Dec Char Binary Dec Char Binary Dec Char"` |
| 6 | `"1 Binary Dec Char Binary Dec Char Binary Dec Char"` |
| 7 | `"1 Operation Operator Comments"` |
| 8 | `"1 Operation Operator Comments"` |

Three separate segments share the identical heading `"1 Binary Dec Char..."`. Two segments share `"1 Operation Operator Comments"`. These are column headers from the ASCII table and the numeric operations table in the textbook — printed in a slightly larger bold font, which exceeds the `median * 1.4` threshold.

**Impact on assessment:**
- Claude has no idea three segments are from the same logical section. Each gets evaluated as if "Binary Dec Char" were a meaningful pedagogical heading.
- Repeated headings make it impossible to use the heading for orientation, degrading `structural_usability` scores.
- The table content itself (see Issue 3) is then evaluated as body prose.

**Fix direction:**
Add a pattern blacklist for headings that match table-column patterns (multiple short words on a single line, all capitalized, no verb). Alternatively, use positional heuristics: table column headers typically appear within a bounding box that is preceded and followed by structured grid-like content — detect and skip these.

---

## Issue 3 — Table Data Linearized as Prose (Severity: HIGH)

**What it looks like in the text sent to Claude:**
```
Addition x+y xandymaybefloatsorints.
Subtraction x-y xandymaybefloatsorints.
Multiplication x*y xandymaybefloatsorints.
Division x/y xandymaybefloatsorints.Theresultisalwaysafloat.
```
And:
```
0100000 32 (cid:2) 1000000 64 @ 1100000 96 '
0100001 33 ! 1000001 65 A 1100001 97 a
```

**Affected segments:** 4 (ASCII table), 7 (numeric operations table), 8 (string operations table).

**What Claude said about segment 7:**
*"The 'table' of operations is presented as a continuous block of text without clear columns or rows, making it unusable."* `structural_usability: 2`

The table is perfectly usable in the original PDF. Claude is penalising the course for a rendering defect in the pipeline.

**Impact on assessment:**
`structural_usability`, `text_readability`, and `pedagogical_clarity` are depressed for any segment containing a table. The penalisation is proportional to how many table rows fall in the segment.

**Fix direction:**
Use pdfplumber's `.extract_tables()` method for each page before extracting text. If tables are detected, either (a) convert them to a Markdown table and embed them in the text, or (b) annotate them as `[TABLE: skipped — visual content]` so the LLM knows not to score readability on them. At minimum, annotate them so Claude doesn't penalise the course for unrenderable content.

---

## Issue 4 — Page Headers & Footers Embedded in Body Text (Severity: HIGH)

**What it looks like:**
```
K.D.Lee,PythonProgrammingFundamentals,UndergraduateTopicsinComputerScience, 1
DOI10.1007/978-1-84996-537-8_1,©Springer-VerlagLondonLimited2011
2 1 Introduction
Fig.1.1 ThePythoninterpreter
1.1
1.2 InstallingPythonandWingIDE101 3
4 1 Introduction
```

These are running page headers (`"4 1 Introduction"`), footers (DOI/copyright), section cross-reference breadcrumbs (`"1.2 InstallingPythonandWingIDE101 3"`), and figure captions (`"Fig.1.1 ThePythoninterpreter"`). pdfplumber extracts everything on the page without distinguishing body from margin content.

**Impact on assessment:**
- The DOI and copyright line appears as if it is course content, confusing `goal_focus`.
- Page numbers embedded mid-paragraph (`"...problems.\nK.D.Lee,...\n2 1 Introduction\nFig..."`) break continuity and make `fluidity_continuity` scores drop.
- Figure captions without the figures (`"Fig.1.1 ThePythoninterpreter"`) score as broken `instructional_alignment`.

**Fix direction:**
Apply a y-coordinate bounding box filter. Page headers typically appear in the top ~8% of the page height; footers in the bottom ~8%. Strip any line whose bounding box falls outside the main body region. Also filter known patterns: lines matching `r'^\d+\s+\d+\s+\w+'` (page_number chapter_number chapter_name) and DOI/ISBN patterns.

---

## Issue 5 — Chapter Number in Margin Fused with Body Text as Heading (Severity: HIGH)

**What it looks like:**

| Segment | "Heading" detected |
|---|---|
| 2 | `"1 Youshouldseesomethinglikethis:"` |
| 3 | `"1 OpenyourIDEifyouhavenotalreadydoneso.IfyouareusingWindowsyoucanselect"` |
| 9 | `"1 ASCIIcharacterequivalentyouusethechr conversionoperator.Forinstance,chr(83)is"` |
| 10 | `"1 Whenprinting,wemayprintasmanyitemsaswelikeononelinebyseparatingeach"` |
| 11 | `"1 numberwewishtoroundthenumberstotwodigitsofprecisionwhentheyareprintedto"` |
| 13 | `"1 9. Completetheprogramstartedinpracticeproblem1.10.Writeaprogramthatasksthe"` |

All these "headings" start with `"1 "` — the chapter number printed in the outer page margin (standard Springer textbook layout). When pdfplumber reads the page, the chapter number sits on the same line as the first line of adjacent body text. Its slightly larger/bolder rendering exceeds the header threshold.

**Critical sub-issue:** Segments 10 and 11 form one sentence split across two segments:
- Seg 10 heading: `"1 Whenprinting,wemayprintasmanyitemsaswelikeononelinebyseparatingeach"`
- Seg 11 heading: `"1 numberwewishtoroundthenumberstotwodigitsofprecisionwhentheyareprintedto"`

These are the first and second halves of the same sentence — the segmenter split a paragraph mid-sentence at the page boundary. The LLM is evaluating two half-sentences as separate pedagogical sections.

**Fix direction:**
Filter headings that match `r'^\d\s+'` (single digit + space, i.e., chapter number prefix). Also: never promote a candidate heading that ends without terminal punctuation and is longer than 60 characters (body sentence, not a heading). Validate that detected headings do not appear mid-paragraph.

---

## Issue 6 — Figure References Without Figures (Severity: MEDIUM)

**What it looks like:**
```
"Fig.1.1 ThePythoninterpreter" — referenced but no image
"Fig.1.2 InstallingPythononWindows" — referenced but no image
"Fig.1.8 TheWingIDE" — referenced but no image
"Fig.1.21 Arun-timeerror" — no image provided
```

The course has at least 23 figures (Fig. 1.1 through Fig. 1.23). None are available to the LLM. Multiple rubric dimensions depend heavily on visual content:
- `structural_usability`: assessed on whether "clear visual representation is provided"
- `instructional_alignment`: expects "figure aids the explanation"
- `example_concreteness`: "reference to Fig.1.13 shows exactly what to do"

**What Claude said (Seg 5):**
*"Referenced figures (Fig. 1.11, 1.13, 1.14) are also missing, further impacting usability."*

The LLM penalises the course for the pipeline's inability to render figures. A course with excellent figures gets a lower score than a course with no figures — because broken references are worse than no references.

**Fix direction:**
Two options: (a) Add a system note to the prompt: `"NOTE: This is text-only extraction. Figures referenced in the text are not available. Do not penalise scores for missing figures or visual aids."` (b) Extract figure captions and embed them as `[FIGURE X.Y: <caption>]` to at least provide the caption context.

---

## Issue 7 — Blank / Unknown Course Metadata (Severity: MEDIUM)

**The metadata injected into every prompt:**
```json
{
  "title": "Python Programming Fundamentals (2010)",
  "author": "Unknown",
  "target_audience": "Unknown",
  "subject": "Unknown",
  "description": "Unknown",
  "prerequisites": [],
  "learning_outcomes": []
}
```

The rubric dimensions most dependent on metadata:
- `prerequisite_alignment` (requires knowing the stated prerequisites)
- `instructional_alignment` (requires knowing the stated learning outcomes)
- `goal_focus` (requires knowing the stated course goals)

With empty metadata, Claude has to infer audience and objectives entirely from the text — which means it is measuring "how well can I guess the course intent from this segment" rather than "does this segment align with stated course intent."

**Impact on assessment:**
`prerequisite_alignment` scores vary from 3 to 9 across segments despite the underlying course having consistent prerequisite alignment. The score reflects Claude's confidence in its inference, not actual alignment. For example, segment 3 gets `prerequisite_alignment: 3` and segment 4 gets `prerequisite_alignment: 9` — likely because segment 4 had cleaner text for Claude to infer from.

**Fix direction:**
The metadata extractor needs improvement. At minimum, use the PDF's table of contents, introduction, or back-cover text to extract `target_audience`, `prerequisites`, and `learning_outcomes`. Failing that, prompt the user to supply these manually before evaluation. Do not proceed with all-Unknown metadata and pretend the dimension is meaningful.

---

## Issue 8 — Assessment/Appendix Content Evaluated as Instructional Content (Severity: MEDIUM)

Segments 12, 13, and 14 contain:
- **Segment 12:** A list of 8 programming exercises (graded homework)
- **Segment 13:** Solutions to practice problems 1.1 through 1.9
- **Segment 14:** Solutions to practice problems 1.10 through 1.15

These are **assessment artefacts**, not instructional narrative. Evaluating them with the instructional rubric produces meaningless results:
- `goal_focus: 9` for an exercise list (exercises have no "goal focus" — they assume the goal was set earlier)
- `fluidity_continuity: 2` for a solutions key (solutions are inherently discontinuous — each is a self-contained answer)
- `text_readability: 2` for segment 14 whose "text" is almost entirely Python code

**What Claude said (Seg 14):**
*"The materials use Python 2.x syntax (e.g., '%d' formatting), which is outdated."* — This is factually wrong; `%d` format strings are valid Python 3. Claude is penalising accurate Python 3 code because it recognises `%` formatting as "old style."

**Fix direction:**
Detect and label segment type: `instructional`, `exercise`, `solution`, `reference_table`. Either (a) apply a different rubric per type, or (b) exclude non-instructional segments from the aggregate score and report them separately.

---

## Issue 9 — CID Character Artifacts (Severity: LOW–MEDIUM)

**What it looks like:**
```
"0100000 32 (cid:2) 1000000 64 @ 1100000 96 '"
```

`(cid:2)` is pdfplumber's fallback representation for a character whose CID (Character ID) could not be mapped to a Unicode character. This typically occurs with custom/embedded fonts that lack a ToUnicode map. In this case the space character `U+0020` in the ASCII table's "space" row is being rendered as `(cid:2)`.

**Impact on assessment:**
Small: creates visual noise. But it signals to Claude that the document has encoding problems, potentially lowering `instructional_alignment` scores even in segments where `(cid:N)` artifacts are rare.

**Fix direction:**
Maintain a lookup table of known CID-to-character mappings for common PDF font encodings. As a fallback, replace `(cid:\d+)` with a placeholder like `[?]` to make the artifact explicit rather than rendering as pseudo-XML.

---

## Issue 10 — Code Blocks Not Demarcated (Severity: LOW–MEDIUM)

**What it looks like:**
```
1 lbs = float(input("Please enter the lbs of water treated: "))
2 ounces = lbs * 16
3 gallons = int(ounces / 128)
4 ounces = ounces - gallons * 128
5 print("That's",gallons,"gallons and", \
6 ounces,"ounces of treated waste water.")
```

Code examples are embedded in body text with only line numbers as markers. The "1", "2", "3"... look like paragraph numbers or enumerated list items to the LLM, not line numbers. When the preceding text already has numbered exercises (Practice 1.1, 1.2...), the collision makes the segment look like a list of 6 items, not a coherent program.

**Impact on assessment:**
`pedagogical_clarity` is underscored because Claude cannot reliably distinguish code from prose. `example_concreteness` may be overscored because Claude sees what looks like a numbered list and assumes good structure.

**Fix direction:**
Use pdfplumber's font detection to identify monospace font regions and wrap them in a `[CODE]\n...\n[/CODE]` block before passing to the LLM. The prompt should instruct the LLM to treat `[CODE]` blocks as programming examples, not prose.

---

## Issue 11 — Mathematical Subscripts Rendered as Trailing Numbers (Severity: LOW)

**What it looks like:**
```
"0011 =3 .Sothehexadecimalrepresentationof01010011 is53 ."
2 16                                            2 16
```

Mathematical subscripts (`₂`, `₁₆`) denoting number bases appear as floating numbers after the main value. `01010011₂ = 53₁₆` becomes `"01010011 2 = 53 16"` — the base subscripts detach and appear as standalone numbers.

**Impact on assessment:**
Minor — Claude correctly identified the context (binary/hex conversion) and scored accurately. But it introduces noise that makes the LLM less confident in technical accuracy assessments.

---

## Summary Table

| # | Issue | Dimensions Corrupted | Severity |
|---|---|---|---|
| 1 | Word concatenation (ligature encoding) | `text_readability`, `pedagogical_clarity`, `structural_usability` | **CRITICAL** |
| 2 | Table headers misidentified as section headings | `structural_usability`, `goal_focus` | **HIGH** |
| 3 | Table data linearized as prose | `structural_usability`, `text_readability`, `pedagogical_clarity` | **HIGH** |
| 4 | Page headers/footers embedded in body text | `fluidity_continuity`, `goal_focus`, `instructional_alignment` | **HIGH** |
| 5 | Chapter number + body text fused as heading / sentence split mid-segment | ALL dimensions | **HIGH** |
| 6 | Figure references without figures | `structural_usability`, `instructional_alignment`, `example_concreteness` | **MEDIUM** |
| 7 | Blank/Unknown course metadata | `prerequisite_alignment`, `instructional_alignment`, `goal_focus` | **MEDIUM** |
| 8 | Assessment content evaluated as instructional content | `goal_focus`, `fluidity_continuity`, `instructional_alignment` | **MEDIUM** |
| 9 | CID character artifacts | `instructional_alignment` (noise) | **LOW–MED** |
| 10 | Code blocks not demarcated | `pedagogical_clarity`, `example_concreteness` | **LOW–MED** |
| 11 | Mathematical subscripts as trailing numbers | `pedagogical_clarity` (noise) | **LOW** |

---

## Reliability Estimate

Based on the above, the following dimensions are currently **unreliable** (score reflects pipeline artifacts, not course quality):

| Dimension | Reliability |
|---|---|
| `text_readability` | Very Low — primarily measures extraction quality |
| `structural_usability` | Low — measures page layout artifacts |
| `pedagogical_clarity` | Low — conflated with word concatenation |
| `instructional_alignment` | Low — metadata is empty; figures are missing |
| `prerequisite_alignment` | Medium — guessed from inferred context |
| `goal_focus` | Medium — page headers and chapter numbers corrupt section intent |
| `fluidity_continuity` | Medium — footer injections break paragraph flow |
| `example_concreteness` | Medium — tables and code not properly marked up |
| `example_coherence` | Medium–High — most reliable dimension |
| `business_relevance` | High — least affected by extraction issues |

The overall score of `text_readability: 2.5` is almost certainly an artifact of pdfplumber failing to reconstruct word boundaries — not a property of the course.

---

## Recommended Priority Order for Fixes

1. **Fix word extraction** — switch to `extract_words()` / `extract_text(use_text_flow=True)` to recover word boundaries. This alone fixes Issues 1 and likely improves all 10 dimensions.
2. **Strip page headers/footers** — y-coordinate bounding box filter. Fixes Issue 4, cascades into Issue 5 (chapter number in margin).
3. **Detect and handle tables** — use `extract_tables()` and convert to Markdown or annotate as `[TABLE]`. Fixes Issues 2 and 3.
4. **Add segment-type detection** — label instructional / exercise / solution / reference segments and exclude non-instructional from the aggregate. Fixes Issue 8.
5. **Improve metadata extraction** — parse ToC and introduction for audience/prerequisites/outcomes. Fixes Issue 7.
6. **Add LLM prompt guardrails** — annotate figures as `[FIGURE X.Y: caption]` and add a note that missing figures are a pipeline limitation, not a course defect. Fixes Issue 6.
7. **Demarcate code blocks** — detect monospace font regions and wrap in `[CODE]` tags. Fixes Issue 10.
8. **Fix CID artifacts** — maintain CID lookup table. Fixes Issue 9.
