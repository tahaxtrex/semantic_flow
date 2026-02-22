## 2026-02-22 — Segmentation Overhaul & Evaluator Fallback Fix

**Type:** Bug fix / Performance improvement
**What changed:**
- `src/segmenter.py`: Replaced loose header heuristic (`size > median + 1.5 OR bold`) with a strict chapter-level rule (`size >= median * 1.4 AND len < 80 chars`). Bold-alone formatting no longer triggers a new segment. Added `_merge_short_blocks()` to consolidate blocks shorter than `min_chars` (600). Raised `max_chars` from 3500 → 8000. Added page-number filtering.
- `src/evaluator.py`: Added `_claude_failure_count` / `_claude_disabled` state. After `_MAX_CLAUDE_FAILURES = 2` consecutive Claude failures the evaluator permanently routes to Gemini for the rest of the run, avoiding N wasted API calls.
- `spec/DECISIONS.md`: Added ADR-006 (Coarse Segmentation) and ADR-007 (Persistent Fallback).
- `spec/TASKS.md`: Added TASK-015 and TASK-016.

**Why it changed:**
- A ~40-page PDF was producing 102 segments (every bold subheading = new segment), making full evaluation impractically expensive.
- Claude was being retried on every segment even when it was clearly rate-limited or unavailable.

**Impact:**
- Expected segment count for a ~40-page PDF: 4–6 (down from ~100).
- Claude failure handling is now O(1) wasted calls instead of O(N).

## 2026-02-22 — Documentation Refactoring
**Type:** Documentation fix
**What changed:** 
- Moved `spec/GUIDE.md` to the root directory and renamed it to `README.md`.
**Why it changed:** User requested that the evaluation tool's manual be available as the primary README in the root.
**Impact:** 
- `spec/GUIDE.md` is now `README.md`
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

## 2026-02-22 — Dimension Count Corrected
**Type:** Documentation fix
**What changed:** 
- Updated `spec/SPEC.md`, `spec/TASKS.md`, and project tracker to note 10 dimensions instead of 8.
**Why it changed:** User observed that the codebase and config actually evaluate 10 dimensions, but the spec documentation incorrectly stated 8.
**Impact:** 
- Documentation alignment

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
