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
- **`main.py`**: The CLI entrypoint that orchestrates the entire pipeline and sets up logging.
- **`metadata.py`**: `MetadataIngestor` checks for external metadata (`.json`, `.txt`, `.html`) matching the course PDF, and falls back to extracting metadata natively from the PDF properties.
- **`segmenter.py`**: `SmartSegmenter` handles deterministic PDF parsing. It extracts font sizes to calculate a median, then treats bolder/larger text as structural headers. Very long blocks are safely chunked on sentence boundaries.
- **`evaluator.py`**: `LLMEvaluator` constructs the assessment prompts based on `config/rubrics.yaml`. It manages the active fallback from Claude 4.6 Sonnet to Gemini 2.5 Flash if rate limits or errors occur.
- **`aggregator.py`**: `ScoreAggregator` takes all evaluated segment scores and computes a strict mathematical average for the entire course.
- **`exporter.py`**: `JSONExporter` saves the fully evaluated verbose dataset (including the exact text submitted for each segment) to disk as validated JSON.
- **`models.py`**: Contains strict `Pydantic` schemas representing the input structures and the expected LLM outputs.

## Usage Instructions
Run the CLI orchestrator natively:

```bash
python -m src.main --input data/courses --output data/output --config config/rubrics.yaml
```

**Workflow:**
1. Drop PDF courses into `data/courses/`.
2. (Optional) Provide manual metadata by adding e.g., `course_1.json` alongside `course_1.pdf` inside the data directory.
3. Run the script.
4. Review logs in standard output or `data/output/evaluate.log`.
5. Check `data/output/` for the final `<course>_evaluation.json` files.

## Understanding the JSON Output
The output JSON strictly embeds:
1. `course_metadata`: Extracted details (Title, Author, Prerequisites, etc.)
2. `overall_score`: The mathematical mean of all segments across the 10 dimensions.
3. `segments`: A deep array of every evaluated block, including:
   - `segment_id` and `heading` (if detected)
   - `text`: The *exact* string passed to the LLM (for perfect reproducibility).
   - `scores`: The integer 1-10 scores for this segment.
   - `reasoning`: The LLM's rationale for each score.
4. `evaluation_meta`: Timestamps, model used, and prompt versioning.
