# SemanticFlow Pipeline: Function-by-Function Walkthrough

This document tracks the data journey from a raw PDF to a structured pedagogical report. Use this to locate specific logic within the codebase.

---

## Step 0: Metadata Extraction (Modular Extension)
**File**: `src/metadata_tool.py`
**Functions**:
- `from_url()`: Scrapes HTML via `BeautifulSoup`.
- `from_pdf()`: Pulls internal PDF dictionary fields.
- `from_json()`: Loads user-defined context.

**Technique**: Decouples "Global Context" (Outcomes, Prerequisites) from the PDF content itself. This ensures the evaluator is judging against the *intended* curriculum, not just the *provided* text.

---

## Step 1: Orchestration & Entry
**File**: `src/cli.py`
**Function**: `main()`

The pipeline begins here. It handles:
1.  **Argument Parsing**: Uses `argparse` to decide whether to process one course (`--course`) or all courses (`--all`).
2.  **Component Initialization**: Instantiates the `PDFExtractor`, `StructuralSegmenter`, and `PedagogicalEvaluator`.
3.  **Main Loop**: Iterates through PDFs, calling the sub-modules in sequence.

---

## Step 2: High-Fidelity Extraction
**File**: `src/extractor.py`
**Key Functions**: 
- `extract_full_text()`: Uses `pdfplumber` to iterate through pages. It doesn't just pull text; it looks for tables via `page.extract_tables()` and flattens them into a pipe-delimited format (`|`) to preserve structural meaning for the LLM.
- `get_bookmarks()`: Accesses the PDF's internal "Outlines" (TOC). This is the metadata "North Star" for our segmentation.

**Technique**: Low-fidelity text extraction but high-fidelity table handling. We avoid OCR here to keep the process fast and deterministic.

---

## Step 3: Structural Segmentation (Non-LLM)
**File**: `src/segmenter.py`
**Key Functions**:
- `segment_course()`: The master logic. It checks if bookmarks exist; if so, it delegates to `_segment_by_bookmarks()`. If not, it falls back to `_segment_by_regex()`.
- `_segment_by_regex()`: Scans the first 5 lines of every page for patterns defined in `config/settings.yaml` (e.g., `^Chapter \d+`). This identifies boundaries without the cost of an LLM call.
- `_split_oversized_segment()`: A safety filter. If a chapter is >20 pages, it hard-splits it into "Parts" to ensure the LLM doesn't lose focus or hit token limits.

**Reasoning**: Using regex and bookmarks for segmentation is **deterministic** and **zero-cost**, ensuring the "intelligent" part of the budget is spent only on the evaluation itself.

---

## Step 4: Structured LLM Evaluation
**File**: `src/evaluator.py`
**Key Functions**:
- `_generate_system_prompt()`: Dynamically builds the system instructions by reading `config/rubrics.yaml`. This ensures the LLM's "mental model" of the 8 rubrics is always synced with your configuration.
- `evaluate_segment()`: The core API bridge. 
    1.  It wraps the Anthropic/Gemini client with `instructor`.
    2.  It sends the segment text and title.
    3.  It enforces the `EvaluationResult` Pydantic model.
    4.  **Failover Logic**: If `Claude` returns an error, it catches the exception and immediately retries the same prompt with `Gemini`.

**Technique**: Unified prompting. All 8 rubrics are evaluated in a single JSON-mode call per segment.

---

## Step 5: Data Validation & Modeling
**File**: `src/models.py`
**Key Models**:
- `PedagogicalScores`: Uses `conint(ge=1, le=10)` to strictly enforce that the LLM *must* return an integer between 1 and 10.
- `EvaluationResult`: Combines the scores with a mandatory `overall_observations` string.

---

## Step 6: Aggregation & Reporting
**File**: `src/cli.py` (Inside `main`)

Once all segments for a course are evaluated:
1.  **Mathematical Average**: The script iterates through the `segment_evals` dictionary and calculates a `simple average` for each of the 8 rubrics.
2.  **CourseReport Generation**: A final `CourseReport` object is created, mapping segment indices to their specific scores.
3.  **Persistence**: The report is saved to `data/reports/` as a permanent JSON record of the course quality.

---

## Summary of Techniques
| Goal | Technique | File |
| :--- | :--- | :--- |
| **Accuracy** | Table-to-Text flattening | `extractor.py` |
| **Cost Control** | Regex-based boundary detection | `segmenter.py` |
| **Reliability** | Claude-to-Gemini Failover | `evaluator.py` |
| **Consistency** | Pydantic Schema Enforcement | `models.py` |
