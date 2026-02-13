# Development Log - SemanticFlow Pedagogical Evaluator

## 2026-02-12
### v0.4 - Initial Scaffolding
- **Status**: Planning complete. Transitioning to implementation.
- **Action**: Created project directory structure.
- **Action**: Initialized `plan.md` (v0.4) and `log.md`.
- **Decision**: Finalized `pdfplumber` for extraction and `instructor` for LLM validation.
- **Decision**: Established structural segmentation (Bookmarks -> Regex -> Page Blocks).

### v1.0 - Core Implementation Complete
- **Status**: Implementation-ready modules finalized.
- **Action**: Created `src/models.py`, `src/extractor.py`, `src/segmenter.py`, `src/evaluator.py`, and `src/cli.py`.
- **Action**: Externalized rubric and settings configurations to YAML.
- **Action**: Implemented Claude-to-Gemini failover logic.
- **Action**: Created `.env.example` for environment setup.

### v1.2 - Metadata Modularization Planning
- **Status**: Planning for independent metadata extraction pipeline.
- **Action**: Updated `plan.md` and `task.md` with modular metadata requirements.
- **Decision**: Explicit metadata arguments will override embedded PDF metadata.
- **Decision**: Metadata context will be prepended to every LLM evaluation call for better pedagogical alignment.
- **Decision**: Implemented strict error handling for CLI-provided metadata paths.

### v1.3 - Metadata Implementation Complete
- **Status**: Modular metadata extraction and injection finalized.
- **Action**: Implemented `src/metadata_tool.py` for multi-source extraction (URL, PDF, JSON).
- **Action**: Updated `src/models.py` with `CourseMetadata` schema.
- **Action**: Modified `src/evaluator.py` to inject global context into system prompts.
- **Action**: Added `--meta` flag and precedence logic to `src/cli.py`.

### v1.1 - Documentation & Memory Finalization
- **Status**: System fully documented for engineering and operations.
- **Action**: Rewrote `plan.md` as a living project memory/evolution log.
- **Action**: Created `README.md` (Engineering focus) and `guide.md` (Operator focus).
- **Action**: Created `pipeline.md` (Function-by-function technical walkthrough).
- **Action**: Created `task.md` (Simulated task board and retrospective).
- **Decision**: Formalized the "Pipe-and-Filter" architectural description.
