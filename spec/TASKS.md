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
- [ ] TASK-016: Add `_merge_short_blocks` unit tests and update `test_segmenter.py`.
  - Spec ref: ADR-006
  - Notes: Test cases: all-short blocks merge into one, mixed blocks emit correct split, single block shorter than min_chars is preserved as fallback (appended to prior or returned alone).
