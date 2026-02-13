# SemanticFlow Evaluator: Engineering Documentation

## Overview
This is a modular Python CLI system designed for the automated pedagogical auditing of educational PDFs. The system follows a **Pipe-and-Filter** architecture: Ingestion -> Segmentation -> Evaluation -> Aggregation.

## Core Architectural Modules

### 1. The Schema Layer (`src/models.py`)
- **Philosophy**: Type-safety is the source of truth.
- **Implementation**: Uses `Pydantic` models.
- **Why**: By defining `PedagogicalScores` as a Pydantic model, we can use the `instructor` library to force the LLM to return a JSON object that fits our exact schema.

### 2. The Extraction Layer (`src/extractor.py`)
- **Library**: `pdfplumber`.
- **Function**: Extracts text and attempts to flatten tables into a pipe-delimited (`|`) text format.
- **Why**: Tables are often where pedagogical examples live; losing them would ruin the "Example Concreteness" rubric.

### 3. The Structural Segmenter (`src/segmenter.py`)
- **Strategy**: 
    1. **Bookmark Extraction**: High-fidelity chapters.
    2. **Regex Scanning**: Fallback for PDFs without metadata.
    3. **Hard-limit**: 20-page chunking to prevent LLM performance degradation.
- **Design Pattern**: Strategy pattern (though implemented within one class) for finding boundaries.

### 4. The LLM Evaluator (`src/evaluator.py`)
- **Engine**: `instructor`.
- **Failover Logic**: 
    - `Primary`: Claude 3.5 Sonnet (Optimized for JSON following).
    - `Secondary`: Gemini 1.5 Pro (Wide context backup).
- **Prompting**: Dynamic system prompt generation. It reads `config/rubrics.yaml` at runtime and injects the rubric descriptions into the prompt.

## Data Schema Hierarchy
Files are stored in a hierarchical manner to allow for incremental processing:
- `data/processed`: Full text dump.
- `data/segments`: Individual chapter JSONs.
- `data/evaluations`: Per-segment LLM output.
- `data/reports`: Final aggregated course-level JSON.

## Design Patterns & Principles
- **Separation of Concerns**: Parsing is entirely decoupled from Evaluation. You can swap the LLM for a local model without changing the PDF logic.
- **Configuration over Code**: Rubric definitions are in YAML, not Python.
- **Fail-Fast**: If the JSON schema doesn't validate, the system attempts a retry or switches providers rather than saving corrupted data.
