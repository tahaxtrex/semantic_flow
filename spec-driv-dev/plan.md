# SemanticFlow Pedagogical Evaluator: Project Brain & Evolution

## 1. Project Genesis (The "Why")
This project was born from the need to evaluate the **pedagogical soundness** of LLM-generated or existing educational materials. While semantic correctness is common, instructional quality (clarity, prerequisite alignment, examples) is often ignored. 

The goal was a **deterministic, low-cost CLI tool** to audit PDFs using LLMs as structured evaluators.

---

## 2. Evolution of Thought & Decision Log

### Phase 1: Conceptual Negotiation (Level 1)
**Initial Confusion**: How to segment without using an LLM (to save cost) while being "smart"?
- **Options**: Simple page splits vs. semantic layout analysis.
- **Clarification**: User requested "structural" segmentation but allowed "low-fidelity" extraction.
- **Decision**: Prioritize PDF Bookmarks (TOC). If missing, use Regex for "Chapter/Module" markers.

### Phase 2: Technical Hardening (Level 2-3)
**Decision: PDF Extraction Engine**
- **Conflict**: Speed vs. Quality.
- **Resolution**: Chose `pdfplumber` because it provides superior table and text positioning compared to `PyPDF2`, which is vital for pedagogical clarity (reading tables correctly).

**Decision: Prompt Strategy & Cost Control**
- **Conflict**: One call per rubric (8 calls total) vs. One unified call.
- **Resolution**: User chose **One Unified Call** for efficiency. To mitigate the "complexity risk" of an LLM evaluating 8 things at once, we use `Claude 3.5 Sonnet` (high reasoning) and `instructor` (strict schema enforcement).

**Decision: The Scoring Scale**
- **Clarification**: User opted for a **1-10 integer scale** without individual rationales to keep tokens low, but added an `overall_observations` field for research-level context.

---

## 3. The "Negotiated" Architecture (Level 4)

### Data Flow
1. **Ingest**: `pdfplumber` extracts raw text and tables.
2. **Segment**: `StructuralSegmenter` looks for TOC Bookmarks. 
   - *Fallback*: Scans for "Chapter X" via Regex.
   - *Safety*: Hard-splits at 20 pages to avoid context overflow.
3. **Evaluate**: `PedagogicalEvaluator` uses the `instructor` library.
   - *Primary*: Claude 3.5 Sonnet.
   - *Secondary*: Gemini 1.5 Pro (Failover).
4. **Aggregate**: Simple average of all segments to produce a `CourseReport`.

### Configuration Strategy
- **Rubrics**: Externalized in `config/rubrics.yaml`. This allows researchers to change what "Clarity" means without touching the Python logic.
- **Settings**: Regex patterns and page limits in `config/settings.yaml`.

---

## 4. Finalized Technical Stack
- **Language**: Python 3.12+
- **PDF**: `pdfplumber`
- **Validation**: `Pydantic` + `instructor`
- **Orchestration**: `argparse` CLI
- **Persistence**: JSON hierarchy (`data/segments` -> `data/evaluations` -> `data/reports`)

---

## 5. Architectural Constraints (Rules to never break)
1. **No Database**: Keep it folder-based and JSON-centric for portability.
2. **Deterministic Segments**: The segment boundaries must be the same every time the script runs on the same PDF.
3. **Strict Validation**: Never accept a non-integer score or a missing rubric field.
4. **Log Changes**: All planning and code structural changes must be recorded in `log.md`.

---

## 6. Metadata Modularization (Extension v0.5)
**Objective**: Decouple metadata from PDF content to allow external context injection (URLs, JSON, External PDFs).

### Confirmed Constraints
- **Independent Pipeline**: Metadata extraction must run separately and save to `data/metadata/`.
- **Normalization**: All sources (URL, PDF, JSON) must map to a single Pydantic schema.
- **Optional CLI Injection**: `src/cli.py` must accept `--meta path/to/metadata.json`.
- **No LLM Inference**: Metadata must be extracted deterministically (scraping/parsing).
- **Precedence Logic**: Explicit CLI metadata argument overrides all embedded metadata.
- **Error Handling**: wrong  metadata filenames passed via CLI must trigger a `stderr` error.
- **Context Strategy**: Prepend full metadata to every segment evaluation call.

### Open Decisions (v0.5)
- **D12**: Merging vs. Replacement for partial metadata.
- **D13**: Default output naming convention.
- **D14**: Scraper library (BS4 recommended).

### Technical Schema (Draft)
- `CourseMetadata`:
    - `title`: str
    - `author`: str
    - `description`: str
    - `learning_outcomes`: List[str]
    - `prerequisites`: List[str]
    - `source_url`: Optional[str]

