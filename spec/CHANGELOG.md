# Project Log & Changelog

## 2026-02-22 — Phase 3 & 4 Completion
**Type:** Scope change & Task Generation
**What changed:** 
- User answered remaining Phase 1 questions (averaging scores, hard crash on fail, verbose JSON exports).
- Updated `spec/DECISIONS.md` adding ADR-004 and revising ADR-002.
- Finalized `spec/SPEC.md` including execution flow, JSON schema, error handling, and testing strategies.
- Generated `spec/TASKS.md` explicitly linked to the spec requirements.
**Why it changed:** Proceeding into Phase 3 and 4 of `GEMINI.md` standard.
**Impact:** 
- The project is now fully specified.
- 14 atomic executable tasks have been written.
- Ready to move to **Phase 5 (Implementation)**.

## 2026-02-22 — Model Selection Updated
**Type:** Requirements revision
**What changed:** 
- Updated `spec/SPEC.md` and `spec/QUESTIONS.md` to specify `Claude 4.6 Sonnet` as the primary model and `Claude 3.5 Sonnet`.
**Why it changed:** User instruction to use Sonnet 4.6 primarily.
**Impact:** 
- Requirements affected: FR-006

## 2026-02-22 — PDF Parsing Strategy Revised
**Type:** Decision revision
**What changed:** 
- Added ADR-005 to prioritize `pdfplumber` over `PyMuPDF`.
- Updated `spec/SPEC.md` and `spec/TASKS.md` to reflect the library change.
**Why it changed:** User instruction to focus on accuracy and research properties over speed/efficiency.
**Impact:** 
- Tasks affected: TASK-001, TASK-005
- ADRs affected: ADR-005 (new)

## 2026-02-22 — Phase 1 Requirements Initiated
**Type:** Scope change & Setup
**What changed:** 
- Converted Phase 0 answers into formal requirements in `spec/SPEC.md`.
- Created Architecture Decision Records inside `spec/DECISIONS.md`.
- Consolidated all planning markdown files inside the `spec/` directory as requested in Q7.
**Why it changed:** Proceeding into Phase 1 of `GEMINI.md` standard.
**Impact:** Established Hybrid Segmentation Strategy (ADR-001). Established Model Cascading pattern for LLMs (ADR-002). Formalized 8 Functional Requirements (FR-001 to FR-008).

## 2026-02-22 — Initial Discovery Phase Commenced
**Type:** Setup
**What changed:** Project documentation initialized based on User trigger `PLANNER`.
**Why it changed:** Requested by user following `GEMINI.md` logic.
**Impact:** Initialized `spec/PROJECT.md` and `spec/QUESTIONS.md`. Prompted 7 clarifying questions.
