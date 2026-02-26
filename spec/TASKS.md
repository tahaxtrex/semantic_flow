# Task Breakdown

## Phase: Setup & Core Scaffold
- [x] TASK-001: Initialize Python project structure (`src/`, `config/`, data directories) and `requirements.txt` / `.env.example`.
  - Spec ref: CON-002, NFR-004
  - Notes: Install libraries like `pdfplumber`, `anthropic`, `google-generativeai`, `pydantic`.
- [x] TASK-002: Create `config/rubrics.yaml` mimicking the 10 dimensions.
  - Spec ref: NFR-002
  - Notes: Requires extracting the 10 rubrics definitions from domain knowledge.
- [x] TASK-003: Implement `main.py` CLI orchestrator with `argparse`.
  - Spec ref: CON-002
  - Notes: Define commands like `--input`, `--output`. Wire up logging to standard out and `data/evaluate.log`.

## Phase: Segmentation & Metadata
- [x] TASK-004: Implement `metadata.py`.
  - Spec ref: FR-001, FR-002, ADR-003
  - Notes: Must check for `{basename}.json|.txt|.html` adjacent to the PDF, and parse it. Include a fallback `extract_metadata_from_pdf()` using regex or pure text parsing.
- [x] TASK-005: Implement `segmenter.py` (Header detection).
  - Spec ref: FR-003, ADR-001, ADR-005
  - Notes: Use `pdfplumber` to read text blocks. Try to identify font-size/formatting heuristics to group blocks by header.
- [x] TASK-006: Implement `segmenter.py` (Safe boundary fallback chunking).
  - Spec ref: FR-004, ADR-001
  - Notes: If a header-grouped block exceeds X characters, iteratively split it on the nearest newline or sentence punctuation (`.`, `?`, `!`).

## Phase: LLM Evaluation
- [x] TASK-007: Implement structural JSON schemas using Pydantic for LLM Responses.
  - Spec ref: FR-007
  - Notes: Maps to the `rubrics.yaml` definitions.
- [x] TASK-008: Implement `evaluator.py` (Claude integration).
  - Spec ref: FR-005, FR-006
  - Notes: Formulate system prompt injecting rubrics + metadata + section text. Force structured JSON outputs.
- [x] TASK-009: Implement `evaluator.py` (Gemini fallback iteration).
  - Spec ref: FR-006, ADR-002
  - Notes: Wrap Claude call in `try/except`. On RateLimit/Auth/Server errors, try Gemini. If Gemini throws exceptions, raise hard error and `sys.exit(1)`.

## Phase: Aggregation & Export
- [x] TASK-010: Implement `aggregator.py`.
  - Spec ref: FR-008
  - Notes: Iterate all Segment evaluations. Calculate the mathematical mean for each of the 10 dimensions.
- [x] TASK-011: Implement `exporter.py`.
  - Spec ref: FR-007, ADR-004
  - Notes: Stitch everything together (metadata, averages, specific segment texts, reasoning) into a single valid JSON file in `--output`.

## Phase: Finalization
- [x] TASK-012: Ensure logging framework records execution events cleanly to standard out.
- [x] TASK-013: Write `test_segmenter.py` and other core unit tests.
- [x] TASK-014: Flesh out `guide.md` with CLI usage instructions and JSON structure definition.

## Phase: Bugfixes & Refinement
- [ ] TASK-015: Validate segmentation granularity on real course PDFs.
  - Spec ref: ADR-006, FR-003
  - Notes: Run `SmartSegmenter` on at least 3 PDFs of varying length. Confirm segment count is in the 4–10 range. Tune `median * 1.4` threshold or `min_chars` if necessary. Document results in `Planning/segmentation_validation.md`.
- [x] TASK-016: Add `_merge_short_blocks` unit tests and update `test_segmenter.py`.
  - Spec ref: ADR-006
  - Notes: Test cases: all-short blocks merge into one, mixed blocks emit correct split, single block shorter than min_chars is preserved as fallback (appended to prior or returned alone).
- [x] TASK-017: Implement `_words_to_lines()` and fix word concatenation.
  - Spec ref: ADR-008, FR-003
  - Notes: Replaces `extract_text_lines(return_chars=True)` with word-object-based reconstruction. Correctly handles ligature-encoded fonts. Strips standalone oversized-font digits (Springer margin chapter numbers).
- [x] TASK-018: Rewrite `_extract_blocks_with_headers()` with page crop and table detection.
  - Spec ref: ADR-010, FR-003
  - Notes: Crops each page to body region (top 10%, bottom 8%). Detects tables via `find_tables()` and annotates as `[TABLE: ...]`. Uses `_words_to_lines()`. Annotates monospace lines with `[CODE]`/`[/CODE]`. Returns `(blocks, page_count)`.
- [x] TASK-019: Implement `_merge_to_target()` and enforce `page_count // 10` segment cap.
  - Spec ref: ADR-009, FR-003, FR-004
  - Notes: Greedy smallest-pair merge ensures `len(blocks) <= max_segments`. Replaces `_merge_short_blocks()` in main path.
- [x] TASK-020: Add extraction disclaimer to `_build_prompt()` in evaluator.
  - Spec ref: FR-005, FR-006
  - Notes: 4-line block after segment text; instructs LLM not to penalise pipeline artifacts (figures, ligature spaces, table markers, code markers).
- [x] TASK-021: Write unit tests for `_words_to_lines()` and `_merge_to_target()`.
  - Spec ref: ADR-008, ADR-009
  - Notes: Test `_words_to_lines()`: empty input, single word, multi-word grouping, oversized-digit stripping, code font detection. Test `_merge_to_target()`: already at target, one over, many over, single block.
- [x] TASK-022: Re-run evaluation on test PDF and validate segment count ≤ 4.
  - Spec ref: ADR-009, FR-003
  - Notes: Run full pipeline on the 40-page Springer test PDF. Verify `grep "Generated.*segments"` in log shows ≤ 4. Spot-check extracted text for absence of concatenated words.
- [x] TASK-023: Implement Evaluator Batching mechanism for processing multiple segments per LLM call.
  - Spec ref: NFR-001, ADR-011
  - Notes: Add `evaluate_batch(metadata, segments)` to `LLMEvaluator`. Loop through arrays of size 5 rather than 1 per API call. Update system prompt.
- [x] TASK-024: Remove model cascading and enforce explicit model routing.
  - Spec ref: FR-006, ADR-002, ADR-014
  - Notes: Drop the Claude fallback logic. Throw a hard HTTP error exception inside of the `evaluator.py` model routing tree if initialization or execution inherently fails.
- [x] TASK-025: Implement non-instructional text detection and bypassing.
  - Spec ref: NFR-001, ADR-012
  - Notes: Identify and tag sections explicitly as `frontmatter`, `exercise`, or `solution`. Have the evaluator bypass the API call for segments that are not `instructional` and return 0 array structures.
- [x] TASK-026: Calculate strictly weighted mathematical averages during Aggregation.
  - Spec ref: FR-008
  - Notes: Exclude 0-score null segments. Multiply each instructional block score by its character length, sum, and divide by the total instructional characters rather than a flat unweighted mean average.
- [x] TASK-027: Harden `Pydantic` schemas in `models.py`.
  - Spec ref: FR-007, ADR-013
  - Notes: Remove any trailing default `= 0` declarations to force strict LLM omissions to be caught and piped to retry triggers rather than succeeding with bad data.
- [x] TASK-028: Refactor `metadata.py` and `main.py` to utilize an explicit `--metadata` arguments flag.
  - Spec ref: FR-001, FR-002
  - Notes: Metadata extraction script must completely ignore auto text matching and only extract metadata if explicitly triggered using the `--metadata` flag. Support `.pdf`, `.json`, `.txt`, and `http` URLs.
- [x] TASK-029: Make `metadata.py` independently executable as a standalone CLI tool.
  - Spec ref: FR-009, ADR-015
  - Notes: Add `if __name__ == "__main__"` block to `metadata.py`. Accept `--pdf`, `--metadata` (optional), and `--output` arguments. Output a human-reviewable JSON file that can be edited and fed back into the evaluator via `--metadata`.
