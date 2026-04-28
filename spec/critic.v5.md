# Pipeline Critic Report v5 — Structural Flow and Regression Issues

**Date:** 2026-04-28
**Source:** Full Codebase Audit
**Purpose:** Document high-severity issues traced to specific logic flaws that result in concrete failure modes during pipeline execution.

---

### HIGH SEVERITY

**1. Desynchronised `block_index` in Segment Classification**
- **Location:** `src/segmenter.py` (Line 257)
- **Flaw:** `_classify_segment` is called with `block_index` derived from the newly `merged_blocks` list, but its internal `_is_before_first_chapter` check compares it against `_first_chapter_block_index` which was calculated on the original, unmerged `raw_blocks` (Line 241).
- **Failure Mode:** When pre-chapter blocks (like TOC and copyright) are merged, the index of the first chapter shifts downwards, causing legitimate early chapters (e.g., titled "Introduction") to evaluate as appearing before the first chapter and be incorrectly bypassed as `preface`.

**2. Missing Cross-Segment Context Injection**
- **Location:** `src/main.py` (Line 122)
- **Flaw:** `evaluator.evaluate_batch(metadata, batch)` is called in the processing loop without ever passing the optional `previous_summaries` argument from already-evaluated batches.
- **Failure Mode:** Because `previous_summaries` is perpetually `None`, the `cross_segment_ctx` string in the prompt builder is always empty, completely disabling the ADR-030 cross-segment repetition detection for the entire pipeline.

**3. Premature Regex Termination on Valid Brackets**
- **Location:** `src/segmenter.py` (Line 130)
- **Flaw:** `_TABLE_ANNOTATION_RE` uses a non-greedy wildcard `(.*?)` that matches up to the very first `]` character it encounters.
- **Failure Mode:** If any table cell contains a closing bracket (e.g., code like `arr[0]` or a citation like `[1]`), the match prematurely terminates, causing `_compute_prose_density` to treat the remainder of the table as raw prose, artificially inflating density and misclassifying genuine reference tables as instructional content.

**4. Separator Bias in Word-Count Heuristic**
- **Location:** `src/segmenter.py` (Line 153)
- **Flaw:** The `len(line.split()) > 10` heuristic evaluates table lines that were explicitly constructed with `" | "` separators, where `.split()` counts every single pipe character as an independent word.
- **Failure Mode:** Genuine multi-column reference tables easily exceed the 10-word threshold solely because of the separators (e.g., a 6-column row has 5 pipe words), causing the table's characters to be added to `prose_in_tables` and destroying the `reference_table` classification.
