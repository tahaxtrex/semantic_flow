# SemanticFlow Pedagogical Evaluator — Technical Specification

## 1. Overview
A deterministic, local Python CLI tool designed to read educational PDFs, deterministically segment their content, extract course metadata, and assess each segment's pedagogical quality across 10 structural dimensions using LLMs (Claude Sonnet primarily, falling back to Gemini). 

## 2. Goals
1. Process course materials locally from a structured folder via CLI.
2. Segment content based on headers, falling back to safe character/page chunks.
3. Use a deterministic strategy (no LLM) for segmentation.
4. Call LLMs efficiently to assess each segment based on the `config/rubrics.yaml` framework.
5. Record output purely in JSON format.
6. Support dual metadata ingestion: prioritize external matching (JSON/HTML/TXT) but fallback to PDF extraction.

## 3. Non-Goals
- Real-time API service or web frontend.
- LLM-based segmentation (to minimize costs).
- Database integration (JSON only).
- Generating new course material.
- Built-in human correlation studies (V1 is evaluation metric generation only).

## 4. Requirements

### Functional Requirements
- **FR-001 (Metadata Ingestion):** The system shall accept external metadata (JSON/HTML/TXT) mapping to a given PDF.
- **FR-002 (Metadata Extraction):** The system shall extract standard metadata (Title, Author, Target Audience, Subject, Source, Description, Prerequisites, Learning Outcomes) from the PDF if external metadata is omitted.
- **FR-003 (Deterministic Segmentation):** The system shall segment the PDF deterministically using header hierarchies, taking visual formatting vs structural headers into account.
- **FR-004 (Segmentation Fallback):** If headers fail or segments are too large, the system shall safely chunk by characters/pages while respecting sentence boundaries.
- **FR-005 (Evaluation):** The system shall query an LLM to evaluate the segment on 10 pedagogical dimensions.
- **FR-006 (Model Cascading & Failure):** The system shall attempt evaluation via Anthropic Claude 4.6 Sonnet first, and fallback to Google Gemini 2.5 Flash upon failure. If both fail, the system shall crash, log the exact error, and halt processing.
- **FR-007 (Output Formatting):** The system shall output the segment scores, reasoning, and the *exact text evaluated* in a strict, validated JSON format.
- **FR-008 (Aggregation):** The system shall aggregate the individual segment assessments into a course-level quality score by mathematically averaging the section scores.

### Non-Functional Requirements
- **NFR-001 (Cost Efficiency):** The system shall cache evaluations and avoid re-evaluating unmodified segments.
- **NFR-002 (Configurability):** The application shall read evaluation criteria entirely from `config/rubrics.yaml`.
- **NFR-003 (Reliability):** API calls must have timeout limits, retry mechanisms, and structured fallback handling.
- **NFR-004 (Environment):** The application must run in Python 3.12+ and manage API keys via `.env` files.

### Constraints
- **CON-001:** No databases allowed. Output must go to `data/output/`.
- **CON-002:** The system must run entirely locally via CLI (no cloud pipeline orchestration tools).

## 5. Architecture
### 5.1 System Diagram
```text
[Input PDFs in /data/courses]
       │
       ▼
[Metadata Ingestor] ◄── (Optionally reads /data/courses/*.json|.txt|.html)
       │
       ▼
[Smart Segmenter] ─── (Header detection -> Sentence-aware fallback)
       │
       ▼
[LLM Evaluator] ────► [Claude API]
       │                   │ (On Fail)
       │                   ▼
       │               [Gemini API]
       │                   │ (On Fail)
       │                   ▼
       │               [Crash & Log]
       ▼
[Score Aggregator] ── (Mathematical average of dimensions)
       │
       ▼
[JSON Exporter] ───► (Outputs to /data/output/*.json)
```

### 5.2 Component Breakdown
1. **CLI Orchestrator (`main.py`):** Validates arguments (e.g. `--input`, `--output`, `--config`), sets up logging, and drives the pipeline.
2. **Metadata Ingestor (`metadata.py`):** Encapsulates the logic to read external files or parse them out of the PDF.
3. **Smart Segmenter (`segmenter.py`):** Utilizes `pdfplumber` to extract structured text. 
4. **LLM Evaluator (`evaluator.py`):** Handles Prompt construction based on `config/rubrics.yaml`, executes async or sync API calls, enforces JSON schema returned from the LLM.
5. **Aggregator (`aggregator.py`):** Calculates course-level score averages and serializes the complete document to JSON.

### 5.3 Data Model (JSON Output Schema)
```json
{
  "course_metadata": {
    "title": "String",
    "author": "String",
    "target_audience": "String",
    "subject": "String",
    "source": "String",
    "description": "String",
    "prerequisites": ["String"],
    "learning_outcomes": ["String"]
  },
  "overall_score": {
    "dimension_1": 0.0,
    "dimension_2": 0.0
  },
  "segments": [
    {
      "segment_id": 1,
      "heading": "String (if available)",
      "text": "Exact text evaluated...",
      "scores": {
        "dimension_1": 0,
        "dimension_2": 0
      },
      "reasoning": {
        "dimension_1_rationale": "String",
        "dimension_2_rationale": "String"
      }
    }
  ],
  "evaluation_meta": {
    "model_used": "Claude 4.6 Sonnet",
    "timestamp": "ISO-8601",
    "prompt_version": "1.0"
  }
}
```

## 6. Technical Decisions
- ADR-001: Deterministic Hybrid Segmentation Strategy
- ADR-002: Active LLM Model Cascading & Hard Failure
- ADR-003: Standalone Metadata Extraction Workflow
- ADR-004: Verbose JSON Output Strategy
- ADR-005: Prioritize Accuracy Over Speed for PDF Parsing

## 7. Error Handling & Edge Cases
- **Missing Metadata Fields:** Will be filled as `"Unknown"` or `null` if the PDF extractor fails. Does not block execution.
- **LLM Rate Limits / 503s:** Cascade to Gemini. If Gemini fails, immediate program termination with strict traceback.
- **Large PDF Segments:** Chunk by max-token length ending on standard sentence punctuation (`.`, `?`, `!`, `\n`).
- **Malformed LLM Output:** `evaluator.py` will force a JSON-schema response from the APIs. If parsing fails, retry 2 times before treating as API failure.

## 8. Testing Strategy
Given the constraints, rigorous unit tests must cover the deterministic nodes:
1. `test_segmenter.py`: ensuring sentence boundary logic and header logic doesn't drop text.
2. `test_metadata.py`: simulating external file reading and PDF extraction mapping.
3. `test_aggregator.py`: mocking segment scores and ensuring averages are computed exactly.

## 9. Deployment & Operations
Operated purely via standard Python environments. Users need to install `requirements.txt` and populate a `.env` file based on a provided `.env.example`.
