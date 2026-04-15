# SemanticFlow Pedagogical Evaluator - User Guide

## Introduction
The SemanticFlow Pedagogical Evaluator is a deterministic, low-cost Python CLI tool. It reads educational PDFs, extracts text while grouping it by semantic headers (using `pdfplumber`), and passes these segments to LLMs for assessment based on 10 strict pedagogical dimensions.

## Setup and Installation
1. Ensure you have Python 3.12+ installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and add your API keys:
   ```bash
   cp .env.example .env
   # Edit .env with your ANTHROPIC_API_KEY and GEMINI_API_KEY
   ```

## Navigating the Codebase
The application is structured cleanly into functional components located in `src/`:
- **`main.py`**: The CLI entrypoint that orchestrates the entire pipeline, sets up logging, and supports explicit LLM model routing via the `--model` flag.
- **`metadata.py`**: `MetadataIngestor` checks for external metadata (`.json`, `.txt`, `.html`) matching the course PDF, then falls back to a two-phase heuristic + LLM pipeline. The heuristic phase (no LLM) extracts the title via font-size heuristic, and author/publisher candidates from the first 3 cover pages only. A single Gemini 2.5 Flash call (Claude Sonnet fallback) then enriches the result with `prerequisites_stated`/`inferred`, `learning_outcomes_stated`/`inferred`, `level`, `target_audience`, TOC, and draft notes. Can also be run **standalone** to produce a reviewable JSON file (see below).

- **`segmenter.py`**: `SmartSegmenter` handles deterministic PDF parsing using a three-tier hierarchy: PDF bookmark outline → UNIT marker scan → font-heuristic. Blocks are greedily merged up to a **30,000-word soft ceiling** (≈40k tokens) — full chapters pass through untouched; only pathological megachapters are split. Segment types include `instructional`, `preface` (routed to Course Gate context), `exercise`, `solution`, `reference_table`, `frontmatter`, `glossary`, `summary`, and `assessment`.
- **`evaluator.py`**: `LLMEvaluator` constructs the assessment prompts based on `config/rubrics.yaml`. It processes segments in batches of 5 to drastically reduce token costs by isolating the rubric into the system prompt. It binds securely to the single requested model per run without silently cascading.
- **`aggregator.py`**: `ScoreAggregator` takes all evaluated segment scores and computes a mathematically correct, length-weighted average for the entire course, excluding bypassed non-instructional segments.
- **`exporter.py`**: `JSONExporter` saves the fully evaluated verbose dataset (including the exact text submitted for each segment) to disk as validated JSON.
- **`models.py`**: Contains strictly validated `Pydantic` schemas without silent `0` defaults, forcing LLMs to return comprehensive metrics or triggering a retry pipeline.

## Usage Instructions
Run the CLI orchestrator natively:

```bash
python3 -m src.main --input data/courses --output data/output --config config/rubrics.yaml --model claude
```

**Workflow:**
1. Drop PDF courses into `data/courses/`.
2. (Optional) Provide manual metadata by adding e.g., `course_1.json` alongside `course_1.pdf` inside the data directory.
3. Choose the model using `--model claude` or `--model gemini`.
4. Run the script.
5. Review logs in standard output or `data/output/evaluate.log`.
6. Check `data/output/` for the final `<course>_evaluation.json` files.

## Standalone Metadata Extraction (Human-in-the-Loop)
For safer, more accurate evaluations, extract metadata **first**, review and edit the resulting JSON, then feed it back into the evaluator.

**Step 1 — Extract metadata (heuristic + AI enrichment — recommended):**
```bash
python3 -m src.metadata --pdf data/courses/Dsa.pdf --output data/courses/Dsa_metadata.json
```
AI enrichment is **on by default** in the standalone extractor (Gemini primary, Claude fallback). To run heuristic-only (no LLM calls):
```bash
python3 -m src.metadata --pdf data/courses/Dsa.pdf --output data/courses/Dsa_metadata.json --no-ai
```

**Step 2 — Review and edit** `data/courses/Dsa_metadata.json` in your editor. Verify the title, correct the author, add prerequisites or learning outcomes the LLM missed.

**Step 3 — Run the full evaluation using your reviewed metadata:**
```bash
python3 -m src.main --input data/courses --output data/output --metadata data/courses/Dsa_metadata.json --model claude
```

The `--metadata` flag also accepts URLs (`http://` or `https://`) pointing to JSON metadata files.

## Understanding the JSON Output
The output JSON strictly embeds:
1. `course_metadata`: Extracted details including `title`, `author`, `publisher`, `level`, `target_audience`, `prerequisites_stated`, `prerequisites_inferred`, `learning_outcomes_stated`, `learning_outcomes_inferred`, `toc` (parsed table of contents), and `draft_notes`.
2. `overall_score`: The mathematical mean of all segments across the 10 dimensions.
3. `segments`: A deep array of every evaluated block, including:
   - `segment_id` and `heading` (if detected)
   - `segment_type`: `instructional`, `preface`, `exercise`, `solution`, `reference_table`, `frontmatter`, `glossary`, `summary`, or `assessment`
   - `text`: The *exact* string passed to the LLM (for perfect reproducibility).
   - `scores`: The integer 1-10 scores for this segment (zero for non-instructional types).
   - `reasoning`: The LLM's rationale for each score.
4. `evaluation_meta`: Timestamps, model used, and prompt versioning.
