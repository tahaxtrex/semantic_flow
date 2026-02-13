# SemanticFlow Development Task Board (Retrospective)

This file tracks the simulated lifecycle of the project from the first conceptual thought to the final documentation.

## Phase 1: Research & Conceptual Mapping
- [x] **Analyze Core Motivation**: Deep dive into `motivation.md` to understand the 8 pedagogical rubrics.
- [x] **Boundary Analysis**: Extract constraints from `roadmap.md` (CLI only, JSON only, no DB).
- [x] **Establish Protocol**: Load and adopt `init.md` behavior (No silent decisions).
- [x] **Define Research Objective**: Clarify that the goal is evaluating *pedagogical validity*, not just text summary.

## Phase 2: Architectural Decision Making
- [x] **Choose PDF Engine**: Comparison of PyPDF2 vs. pdfplumber. 
    - *Decision*: `pdfplumber` for better table/layout handling.
- [x] **Design Segmentation Logic**: 
    - *Thought*: Should we use LLM for chapters? 
    - *Decision*: No, too expensive. Use Bookmarks + Regex fallback.
- [x] **LLM Selection**:
    - *Decision*: Claude 4.6 Opus as Primary (best JSON adherence).
    - *Decision*: Gemini 1.5 Pro as Backup.
- [x] **Orchestration Strategy**: Chose `instructor` library to bridge Pydantic and LLM calls.

## Phase 3: Technical Scaffolding
- [x] **Folder Architecture**: Set up `data/`, `config/`, and `src/` hierarchy.
- [x] **Define Data Models**: Create `src/models.py` with strict Pydantic constraints (1-10 integer ratings).
- [x] **Externalize Configuration**: Move rubric definitions and regex patterns into YAML files for "zero-code" research iterations.

## Phase 4: Implementation - The Processing Pipeline
- [x] **Develop Extractor**: Implement `src/extractor.py` with table-flattening logic.
- [x] **Develop Segmenter**:
    - [x] Implement Bookmark-based splitting.
    - [x] Implement Regex-based fallback for PDFs without TOC.
    - [x] Implement 20-page "Hard-split" safety mechanism.
- [x] **Develop Evaluator**:
    - [x] Implement dynamic prompt builder.
    - [x] Integrate Claude 4.6 Opus via Anthropic API.
    - [x] Integrate Gemini 1.5 failover logic.

## Phase 5: Implementation - CLI & Aggregation
- [x] **Build CLI Orchestrator**: Create `src/cli.py` to manage the end-to-end loop.
- [x] **Aggregation Logic**: Implement simple average calculation for course reports.
- [x] **Persistence Layer**: Ensure valid JSON output for segments, evaluations, and reports.

## Phase 6: Documentation & Memory 
- [x] **Living Memory Update**: Overhaul `plan.md` to capture project evolution and confusion.
- [x] **Engineering Handover**: Create `README.md` with a focus on internal architecture.
- [x] **Operational Guide**: Create `guide.md` for end-user execution.
- [x] **Technical Mapping**: Create `pipeline.md` for function-level traceability.
- [x] **Simulated Task Board**: Create `task.md` (this file).

## Phase 7: Metadata Modularization (Complete)
- [x] **Define Metadata Schema**: Create `CourseMetadata` Pydantic model in `src/models.py`.
- [x] **Develop Metadata Extractor Utility**:
    - [x] Implement `src/metadata_tool.py` CLI.
    - [x] Implement PDF embedded extraction.
    - [x] Implement JSON file loading.
    - [x] Implement URL scraping (BS4).
    - [x] Implement External Syllabus PDF extraction.
- [x] **Update Evaluation Pipeline**:
    - [x] Modify `src/evaluator.py` to accept `CourseMetadata`.
    - [x] Inject metadata into the LLM system prompt.
- [x] **CLI Integration**:
    - [x] Add `--meta` flag to `src/cli.py`.
    - [x] Implement strict file existence checks for metadata.
- [x] **Update Documentation**:
    - [x] Update `guide.md` with metadata usage instructions.
    - [x] Update `pipeline.md` with the metadata extraction step.

---

## Future Backlog (Pending)
- [ ] **M4: Prompt Strategy Comparison**: Add logic to test Zero-Shot vs Few-Shot.
- [ ] **M5: Human Correlation**: Build a script to compare LLM scores vs a provided "human_scores.json".
- [ ] **Parallel Processing**: Optimize `cli.py` to evaluate segments in parallel (risk: rate limits).
