# Phase 0: Discovery Questions

## Q-001: Metadata Extraction
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** What specific metadata fields are required to be extracted from PDFs, JSON, or HTML (e.g., Title, Author, Target Audience, Subject, Prerequisites)?
**Answer:** Title, author, target audience, subject, source, description, prerequisite, learning outcomes.

## Q-002: Deterministic Segmentation Rules
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** How precisely should deterministic segmentation function? 
**Answer:** Primary strategy should be header-based (smartly distinguishing formatting headers from structural ones). Fallback to chunks of characters/pages but respecting sentence boundaries to avoid cutting mid-text.

## Q-003: LLM Integration
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Which LLM provider or API will be used?
**Answer:** Primarily Claude 4.6 Sonnet (via API). Fallback to Gemini 2.5 Flash. API keys will be in `.env`.

## Q-004: Rubrics File
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Where is `rubrics.yaml`?
**Answer:** It is/will be available in the `config/` folder.

## Q-005: Human Correlation Study Support
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Does the CLI need to handle the human correlation study?
**Answer:** No. Focus only on building the automated V1 assessment tool that yields a JSON output. 

## Q-006: Execution Pipeline Boundaries
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Does the CLI tool invoke the metadata script automatically?
**Answer:** Metadata extraction should be an independent step to ensure match, accepting JSON/HTML/TXT. If external metadata isn't provided, the main assessment script should automatically invoke a built-in PDF metadata extractor as a fallback.

## Q-007: Structure & Files Naming
**Phase:** Discovery
**Date:** 2026-02-22
**Status:** Answered

**Question:** Root folder vs `spec/` folder for spec docs?
**Answer:** Keep the spec md files (`guide.md`, `log.md`, `tasks.md`, `plan.md`) in the `spec/` folder.

---

# Phase 1: Requirements Questions

## Q-008: Evaluation Aggregation
**Phase:** Requirements
**Date:** 2026-02-22
**Status:** Answered

**Question:** How should the final "course-level" score be aggregated from the individual section scores? (e.g. simple mathematical average, or a final LLM summarization call?)
**Answer:** By mathematically averaging the section scores across the 10 dimensions.

## Q-009: API Failure Handling
**Phase:** Requirements
**Date:** 2026-02-22
**Status:** Answered

**Question:** How should the system handle a complete API failure (where both Claude and Gemini fail/timeout for a specific chunk)?
**Answer:** Crash gracefully: return an error message describing the actual error and stop the process entirely.

## Q-010: JSON Output Granularity
**Phase:** Requirements
**Date:** 2026-02-22
**Status:** Answered

**Question:** Should the final output JSON embed the exact text of every evaluated segment, or just the segment titles/IDs?
**Answer:** The exact text of every segment evaluated must be embedded in the final JSON output.
