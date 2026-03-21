Looking at the critic.md file to understand the exact style before rewriting.Good, I have the exact style. Here's the rewrite:

---

# Assessment Quality Critic Report

**Date:** 2026-03-13
**Sources:** `firstpart.pdf_evaluation.json`, `secondpart.pdf_evaluation.json`
**Run:** firstpart: 17 segments (12 instructional scored); secondpart: 12 segments (11 instructional scored) — both evaluated by Gemini-2.5-Flash
**Purpose:** Identify flaws in the evaluation pipeline's assessment logic and segment classification that corrupt the LLM's scores — independent of actual course quality.

---

## TL;DR

The evaluations are compromised by two compounding problems. The segment classifier is mislabelling content in both directions: substantive instructional segments are being zeroed out as `frontmatter`, while assessment artefacts (glossaries, review questions, exercise banks) are being scored on instructional rubrics. A running page header string (`"Access. The future of education."`) appears to be the primary trigger for false frontmatter classification, silently discarding 3+ scored segments from `firstpart.pdf`. The Course Gate compounds this by evaluating each PDF file as if it were a complete standalone course, penalising structural gaps that are actually artefacts of how the source material was split across files.

---

## Issue 1 — Running Page Header Triggering False Frontmatter Classification (Severity: CRITICAL)

**What it looks like:**

| Segment ID | segment_type assigned | Actual content |
|---|---|---|
| 5 (`firstpart.pdf`) | `frontmatter` → zeroed | Full introduction to IS components, COVID-19 case study, Figure 1.2 |
| 10 (`firstpart.pdf`) | `frontmatter` → zeroed | COBIT, ITIL, McKinsey 7-S, SFIA frameworks — 3 full pages of instructional content |
| 4 (`firstpart.pdf`) | `frontmatter` → zeroed | Author bio transition + EIS/MIS/TPS IS types introduction |

Every segment in `firstpart.pdf` carries the heading `"Access. The future of education."` — an OpenStax running page header that appears at the top of every page. The segmenter is detecting this string as the segment's heading, which then matches the frontmatter classifier and zeros the entire segment out.

**What the scores show:**
- Segment 5 (`frontmatter`, zeroed): contains the COVID-19 Kerala mobile health case study and the complete 5-component IS breakdown with Figure 1.2 — this is a primary instructional segment. Its `example_concreteness` and `pedagogical_clarity` are not low; they simply don't exist in the output.
- Segment 10 (`frontmatter`, zeroed): covers COBIT's five-process governance framework, McKinsey 7-S, SFIA's seven-level responsibility model — approximately 3 pages of dense framework content that should score high on `pedagogical_clarity` and `structural_usability`.
- 5 of 17 segments in `firstpart.pdf` are excluded. At least 3 of those 5 contain genuine instructional content.

**Root cause:**
ADR-010 specifies that running headers should be stripped via y-coordinate crop (`page.within_bbox`) *before* word extraction. The crop is working for the text body — the header text does not appear in segment bodies. However, the heading detector is running *after* the crop, on the first high-font-size word cluster it finds, and the OpenStax header sits at a y-position just inside the crop boundary or is being detected from a different pass. The result is that `"Access. The future of education."` becomes the recorded `heading` field for the segment, which the frontmatter classifier then correctly (but incorrectly) acts on.

**Impact on assessment:**
Module Gate scores for `firstpart.pdf` are computed from 12 segments instead of at least 15. The excluded segments likely scored differently from the surviving ones — their absence introduces a systematic bias of unknown direction into every averaged dimension.

**Fix direction:**
The heading detection pass must be subject to the same content filters as body extraction. Any candidate heading that matches known running header patterns (publisher taglines, DOI strings, "Access for free at", "Access. The future of education.") must be rejected before it is promoted to the segment's `heading` field. The frontmatter classifier should never fire on a string that is a page-level print artifact rather than structural document metadata. A secondary safeguard: if `heading` matches a known running header string, inherit the heading from the previous segment or set it to `null` rather than triggering a type change.

---

## Issue 2 — Assessment and Reference Segments Not Being Tagged Out (Severity: HIGH)

**What it looks like:**

| Segment ID | segment_type | Content | Module Gate scores |
|---|---|---|---|
| 11 (`secondpart.pdf`) | `instructional` | Key terms glossary + module summary bullets | `example_concreteness: 1`, `example_coherence: 1`, `goal_focus: 9` |
| 12 (`secondpart.pdf`) | `instructional` | Review questions, check-your-understanding, application questions | `goal_focus: 10`, `instructional_alignment: 10` |
| 16 (`firstpart.pdf`) | `instructional` | Module summary bullets + 8 review questions (multiple choice) | `goal_focus: 4`, `example_concreteness: 4`, `example_coherence: 4` |
| 17 (`firstpart.pdf`) | `instructional` | 17 review questions + check-your-understanding + application questions | `goal_focus: 9`, `instructional_alignment: 9` |

None of these are instructional content. They are glossaries, summary lists, and assessment banks. ADR-012 mandates that non-instructional segments be bypassed and excluded from the course average. These segments are not being bypassed.

**What the LLM said:**
- Segment 11 (`secondpart.pdf`): *"This segment consists of definitions and high-level summaries; it does not provide any concrete, relatable, or plausible real-world examples."* `example_concreteness: 1`, `example_coherence: 1` — the model correctly identified the problem but still scored it.
- Segment 12 (`secondpart.pdf`): scored `goal_focus: 10`, `instructional_alignment: 10` — a review question bank cannot demonstrate instructional goal focus; it assumes it. These scores are structurally meaningless and will inflate those dimensions in the aggregate.
- Segment 17 (`firstpart.pdf`): `goal_focus: 9`, `pedagogical_clarity: 8` for a page of multiple choice questions. Claude is effectively scoring how well the questions test the material, not whether the material teaches it.

**Root cause:**
The `SmartSegmenter`'s type detection is not catching end-of-chapter structures: glossary sections (identifiable by alphabetically-ordered term–definition pairs), summary sections (identifiable by a "Summary" heading followed by bullet points), and review question banks (identifiable by numbered questions with lettered answer options). These are reliably structured in OpenStax textbooks but the classifier appears to only detect frontmatter and exercise sections explicitly, leaving glossaries and assessment banks classified as `instructional` by default.

**Impact on assessment:**
Glossary segments with `example_concreteness: 1` drag down that dimension's average across the course. Conversely, review question segments with `goal_focus: 9–10` and `instructional_alignment: 9–10` artificially inflate those dimensions. Both distortions compound in the Module Gate's `overall_score`.

**Fix direction:**
Extend `segment_type` detection to cover at minimum: `glossary` (heading matches "Key Terms" / "Glossary"; body consists of term–definition pairs), `summary` (heading matches "Summary" / "Chapter Summary"; body consists of bullet points prefixed with section numbers), and `assessment` (body consists of numbered questions with lettered options a/b/c/d). All three types should be bypassed by `LLMEvaluator` and excluded from `ScoreAggregator`, consistent with the existing handling of `exercise` and `frontmatter` types.

---

## Issue 3 — Course Gate Evaluating File Fragments as Complete Courses (Severity: HIGH)

**What it looks like:**

From `secondpart.pdf` Course Gate:
- `fluidity_continuity: 4.0` — *"The most significant detractor from fluidity and continuity is the complete absence of Module 8, which creates an abrupt and unexplained jump in the course narrative."*
- `structural_usability: 5.0` — *"the provided 'FRONTMATTER' is fragmented and does not serve as an effective course overview or guide. Key elements for structural usability, such as explicit technical requirements, support resources, or a comprehensive table of contents, are not evident."*

`secondpart.pdf` is not a complete course. It is the second file of a two-file split of the same textbook. Module 8 exists — it is in `firstpart.pdf`. The Table of Contents exists — it is in `firstpart.pdf`. The Course Gate has no awareness of this and evaluates `secondpart.pdf` as if it were a standalone self-contained course, then penalises it for structural incompleteness that the pipeline itself introduced by splitting the book.

**Root cause:**
Q-014 in QUESTIONS.md (Course Gate scope) is still open. There is no implemented mechanism to pass multi-file context to the Course Gate, and no metadata field indicates whether the input is a complete course or a fragment. The Course Gate receives only the frontmatter and module summaries of the current file, so a mid-book fragment will always appear to have a missing introduction, a broken table of contents, and absent early modules.

**Impact on assessment:**
`fluidity_continuity` and `structural_usability` for `secondpart.pdf` are measuring the quality of the file split, not the quality of the course. A score of 4.0 on fluidity for a textbook that the Module Gate scores 8+ on nearly every content dimension is an incoherent result — and it is entirely a pipeline artefact.

**Fix direction:**
Two options. (a) **Multi-file merging:** Before the Course Gate runs, merge the frontmatter, Table of Contents, and module summary lists from all files belonging to the same course into a single Course Gate context payload. This requires a mechanism to associate files as belonging to the same course — either by naming convention, a config file, or a user-supplied grouping flag. (b) **Fragment flag:** Add a `is_partial_course: true` field to the evaluation metadata, and inject a corresponding instruction into the Course Gate system prompt: *"NOTE: This evaluation covers a partial segment of a larger course. Do not penalise scores for absent modules, incomplete tables of contents, or missing introductory material not present in this file."*

---

## Issue 4 — `instructional_alignment` Remains in the Module Gate (Severity: MEDIUM)

**What it looks like:**

`instructional_alignment` appears as a scored rubric in the Module Gate output of both files:
- `firstpart.pdf` Module Gate: `instructional_alignment: 8.56`
- `secondpart.pdf` Module Gate: `instructional_alignment: 8.88`

Per ADR-016, `instructional_alignment` was assigned to the Course Gate in the two-gate restructure. It should not be present in Module Gate schema or scoring at all.

**What the LLM said:**
For segment 6 of `firstpart.pdf`: *"The instructional materials are current and accurate, covering key concepts like Web 2.0, social media, future IS trends..."* — the model is assessing content currency and accuracy, not alignment with stated learning outcomes. With `learning_outcomes: ["Define the key concepts in information systems", "Discuss the historical evolution of information systems"]` being the only two outcomes available, the model has insufficient context to score true instructional alignment at the segment level and is substituting a general content quality judgment instead.

**Root cause:**
ADR-016 was accepted as an architectural decision but open questions Q-016 through Q-022 remain unanswered. The rubric split was decided but not yet implemented in `models.py` or the system prompt. `SectionScores` still includes `instructional_alignment` as a Module Gate field.

**Impact on assessment:**
`instructional_alignment` scores in the Module Gate are measuring content quality with minimal metadata context — the same proxy problem documented in critic.md Issue 7 for the earlier Python textbook run. The scores are not invalid (they correlate with segment quality) but they are not measuring what the rubric name claims. This creates a labelling problem for the research output.

**Fix direction:**
Remove `instructional_alignment` from `ModuleScores` in `models.py` and from the Module Gate section of `rubrics.yaml`. Add it to `CourseScores` and the Course Gate system prompt. This is a direct implementation task for ADR-016, contingent on resolving Q-016.

---

## Summary Table

| # | Issue | Dimensions Corrupted | Severity |
|---|---|---|---|
| 1 | Running page header triggering false frontmatter classification | ALL dimensions (3+ segments silently zeroed) | **CRITICAL** |
| 2 | Assessment and glossary segments not tagged as non-instructional | `goal_focus`, `example_concreteness`, `example_coherence`, `instructional_alignment` | **HIGH** |
| 3 | Course Gate evaluating file fragments as complete courses | `fluidity_continuity`, `structural_usability` | **HIGH** |
| 4 | `instructional_alignment` still in Module Gate schema | `instructional_alignment` (label mismatch) | **MEDIUM** |

---

## Reliability Estimate

| Dimension | Reliability | Primary threat |
|---|---|---|
| `goal_focus` | Medium — inflated by review question segments scoring 9–10 | Issue 2 |
| `text_readability` | Medium–High — OpenStax PDFs are well-encoded, extraction is clean | — |
| `pedagogical_clarity` | Medium–High — reliable where segments are correctly classified | Issue 1 |
| `example_concreteness` | Medium — dragged down by glossary segments scoring 1 | Issue 2 |
| `example_coherence` | Medium — same glossary distortion | Issue 2 |
| `instructional_alignment` | Medium — scoring content quality, not stated-outcome alignment | Issue 4 |
| `fluidity_continuity` (Course Gate) | Low for split files — penalises the file split, not the course | Issue 3 |
| `structural_usability` (Course Gate) | Low for split files — same cause | Issue 3 |
| `prerequisite_alignment` (Course Gate) | High — metadata is populated and the model uses it correctly | — |
| `business_relevance` (Course Gate) | High — least affected by any of the above issues | — |

---

## Recommended Priority Order for Fixes

1. **Fix running header heading detection** — reject known publisher/platform strings from the heading field before the frontmatter classifier fires. Recovers 3+ silently zeroed instructional segments in `firstpart.pdf`. Fixes Issue 1.
2. **Extend segment_type detection to glossary, summary, and assessment** — pattern-match "Key Terms", "Summary", and numbered-question-with-lettered-options structures. Bypasses and excludes them from scoring. Fixes Issue 2.
3. **Add multi-file or fragment awareness to the Course Gate** — either merge cross-file context before the Course Gate runs, or inject a fragment disclaimer into the system prompt when a file has no Table of Contents. Fixes Issue 3.
4. **Remove `instructional_alignment` from Module Gate schema** — implement the ADR-016 rubric split in `models.py` and `rubrics.yaml`. Fixes Issue 4.