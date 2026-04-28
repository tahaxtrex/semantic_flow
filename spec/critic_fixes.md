# Pipeline Critic Fixes — Architectural Interaction & Code Corrections

## Architectural Interaction Analysis

The four prioritized issues are not independent bugs; they form a cascading chain of data corruption that flows from the segmentation pipeline up to the final Course Gate capstone evaluation:

1. **The Upstream Classifier (Fix 2):** When `_compute_prose_density` incorrectly strips legitimate prose because `pdfplumber` mis-tagged it as a `[TABLE:]`, dense instructional segments are misclassified as `reference_table`.
2. **The Module Gap (Interaction):** These misclassified segments bypass the Module Gate. They receive no scores and generate no summaries.
3. **The Course Gate Contamination (Fix 1 & 4):** The Course Gate receives a truncated, Swiss-cheese version of the book's narrative. Furthermore, because the Module Gate was penalizing `goal_focus` for segmentation artifacts (Fix 1), the quality signals the Course Gate *does* receive are artificially depressed by pipeline noise.
4. **The Calibration Collapse (Fix 3):** Because the Course Gate is fed fragmented summaries and artificially depressed Module Gate scores, any attempt to calibrate `business_relevance` or `instructional_alignment` against the provided context is fundamentally flawed. We cannot establish valid 9–10 anchors (Fix 3) until the text extraction (Fix 2) and artifact isolation (Fix 1) guarantee a clean signal.

The fixes below address the root causes at each stage of this cascade.

---

## 1. Double-Penalty Problem (Architectural Isolation)

**Root Cause:** The Module Gate prompt builder explicitly injects the extracted heading into the LLM's context window for every segment. The LLM naturally compares the heading to the body text. Modifying the rubric criteria cannot override the LLM's innate text-comparison behavior. 

**Architectural Decision:** We must choose between (a) stripping the heading entirely and relying on `metadata.title` + `learning_outcomes` for topic context, or (b) making two separate scoring calls (one structural, one content). Given ADR-011 and ADR-021 (which mandate batching and single-pass scoring to minimize API costs), a two-pass approach is prohibitively expensive. We must strip the heading.

**Tradeoff & Mitigation:** Stripping the heading completely leaves `goal_focus` anchored only to the full course metadata. For a 300-page textbook, this is an extremely coarse signal (e.g., scoring a "Database Normalization" chapter against "Foundations of Information Systems"). 
*Suggested Mitigation:* In a future update, extract and pass module-level learning outcomes or a sanitized `[Topic Focus]` derived from the TOC, providing a local anchor without triggering the LLM's literal heading-vs-body penalty.

**Fix:** Remove the heading from the segment payload in the user prompt.

**`src/evaluator.py` (around line 446):**
```python
         user_prompt = "Score the following segments:\n\n"
         for i, s in enumerate(segments):
             user_prompt += f"--- SEGMENT ID: {s.segment_id} ---\n"
-            user_prompt += f"Heading: {s.heading or 'None'}\n"
             # ADR-030: inject previous segment summary for cross-segment awareness
             if i > 0 and segments[i-1].segment_id in [seg.segment_id for seg in segments[:i]]:
```

---

## 2. Reference Table Classifier (Upstream Measurement Fix)

**Root Cause:** `_compute_prose_density` aggressively strips all text within `[TABLE:]` markers. If `pdfplumber` mis-identifies a text block with borders as a table, legitimate instructional prose is removed from the numerator, artificially tanking the density calculation and causing the `reference_table` over-classification.

**Empirical Caveat:** The `>10 words` heuristic below is a starting point. A descriptive table cell (e.g., "Returns the normalized value of the input tensor after batch processing") could easily exceed 10 words. Before finalizing this logic, an empirical analysis mapping the line-length distribution of known-good tables vs. known-bad miswrapped segments must be run to find the true statistical boundary.

**Fix:** Introduce a sentence-length heuristic inside the table-stripping logic. If a "table cell" contains a long string of words, it is miswrapped prose and must be counted toward the density. Note the capture group used to safely extract the table's inner content without fragile string replacement.

**`src/segmenter.py` (around line 127):**
```python
 def _compute_prose_density(text: str) -> float:
     if not text:
         return 0.0
     total = len(text)
+
+    # Detect miswrapped prose masquerading as table content
+    prose_in_tables = 0
+    # Ensure _TABLE_ANNOTATION_RE uses a capture group: r'\[TABLE:(.*?)\]'
+    for match in _TABLE_ANNOTATION_RE.finditer(text):
+        table_content = match.group(1)
+        for line in table_content.splitlines():
+            # Heuristic: A line inside a table with >10 words is likely prose.
+            # (Note: This threshold should be empirically validated against a dataset of known tables)
+            if len(line.split()) > 10:
+                prose_in_tables += len(line)
+
     # strip table annotations (pdfplumber-injected markers)
     stripped = _TABLE_ANNOTATION_RE.sub('', text)
     # strip [CODE]...[/CODE] regions
     stripped = _CODE_BLOCK_RE.sub('', stripped)
     # strip bullet/numbered list lines
     prose_lines = [
         ln for ln in stripped.splitlines()
         if ln.strip() and not _BULLET_LINE_RE.match(ln)
     ]
-    prose_chars = sum(len(ln) for ln in prose_lines)
+    prose_chars = sum(len(ln) for ln in prose_lines) + prose_in_tables
     return prose_chars / total if total else 0.0
```

---

## 3. Calibration Anchors (Independent, Curated Baselines)

**Root Cause:** The prompt lacks 9–10 anchors for the Module Gate rubrics, causing the LLM to cluster scores. Effective anchors must be manually curated, adversarial (defining the edge between 9 and 10), and completely independent of the dataset being evaluated. 

**Fix:** Inject explicit, non-circular 9–10 anchors into the `CALIBRATION ANCHORS` section, along with a domain-generalization instruction.

**`src/evaluator.py` (around line 360):**
```python
+  When applying the anchors above to non-CS content, identify the equivalent structural 
+  property in the target domain rather than matching the surface subject matter.
+
   goal_focus:
     8 — Stays on-topic throughout; every paragraph directly serves the stated learning goal
+    10 — Exemplary: "The segment introduces Dependency Injection and every paragraph, code snippet, and warning directly serves to explain its mechanics and why it solves tight coupling. Zero digressions."

   text_readability:
     8 — Clear, well-paced, grammatically correct prose; every code block preceded or followed by plain-language explanation
+    10 — Professional fluid prose: Technical terms are integrated seamlessly. Sentences vary in length to maintain engagement. No dense walls of uninterrupted text.

   pedagogical_clarity:
     8 — Every new term defined on first use; notation is consistent from start to finish
+    10 — Anticipates student confusion: "You might wonder why we don't just use a global variable here. If we do..." Defines concepts precisely before showing the code that relies on them.

   example_concreteness:
     8 — Realistic, domain-grounded scenarios: student records, inventory system, sales data, employee payroll
+    10 — High-fidelity industry scenarios: Code calculates a subtotal with tax and shipping for an 'E-commerce Shopping Cart', explicitly handling edge cases like out-of-stock items, demonstrating real-world constraints.

   example_coherence:
     8 — Examples share a consistent domain or running scenario that accumulates across the segment
+    10 — The entire segment builds a single 'Task Management API'. It starts with defining the data model, adds routing, then database integration, maintaining perfect narrative continuity.
```

---

## 4. Course Gate Validation (Conformance & Regex Fix)

**Verification of ADR-016 Conformance:**
1. **Single capstone call:** `src/main.py` L160 calls `evaluator.evaluate_course(metadata, segments)` exactly once after the module batch loop.
2. **Distilled input:** `src/evaluator.py` L622 constructs the Course Gate prompt by concatenating `seg.summary` for all instructional modules.
3. **Separate rubrics:** `config/rubrics.yaml` L200 introduces the `course_rubrics` dictionary, cleanly separating them from `module_rubrics`.
4. **Instructional Alignment removal:**
   - **`src/models.py` L58:** Added to `CourseScores` (and removed from `ModuleScores`).
   - **`src/aggregator.py` L23:** Documented as `ADR-028: instructional_alignment lives in CourseScores (moved from Module Gate)`.
   - **`src/aggregator.py` L97:** Explicitly mapped from `course_assessment.scores.instructional_alignment`.

**Course Gate Regex Bug Fix:**
The `_FIRST_CHAPTER_RE` contained a duplicate `unit` in the alternation group and missed common academic formats like "Lesson 1" or "Part 1".

**`src/evaluator.py` (around line 721):**
```python
         _FIRST_CHAPTER_RE = _re.compile(
-            r'^(chapter\s*(0|1|i|one)\b|module\s*(0|1|i|one)\b|unit\s*(0|1|i|one)\b|introduction\b|foundations?\b|fundamentals?\b|getting\s+started\b)',
+            r'^(chapter|module|unit|part|lesson)\s*(0|1|i|one|first)\b'
+            r'|^(introduction|foundations?|fundamentals?|getting\s+started)\b',
             _re.IGNORECASE,
         )
```

---

## 5. Deployment & Cache Invalidation

Because the Module Gate score depression feeds directly into corrupted Course Gate signals, applying Fixes 1–4 to the codebase will not fix the evaluation metrics of already-processed courses unless their cached scores are discarded.

**Critical Action:**
Before deploying these fixes, all previously generated evaluations must be invalidated. The Course Gate must not be allowed to consume artificially depressed Module Gate scores generated under the old broken conditions.

- Ensure `data/output/*_evaluation.json` files are wiped before the next pipeline run.
- Alternatively, introduce a `--force-reeval` CLI flag in `src/main.py` to explicitly overwrite cached partial evaluations when foundational prompt/segmentation logic changes.