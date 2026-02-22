# Architecture Decision Records (ADRs)

## ADR-001: Deterministic Hybrid Segmentation Strategy

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The core research principle specifies deterministic, non-LLM segmentation to minimize costs and ensure absolute reproducibility (FR-003, FR-004). However, PDF parsing is notoriously messy with headers sometimes only acting as visual formatting.

**Options Considered:**
1. *Naive character splitting:* Guaranteed size, but splits mid-sentence and ignores logical boundaries.
2. *Strict Header splitting:* High logical accuracy, but can fail if headers are purely visual (no PDF outline) creating massive single chunks.
3. *Hybrid (Header -> Safe Chunk):* Attempt to split by structural headings. If a resulting segment exceeds a maximum length threshold, apply sentence-boundary-aware chunking.

**Decision:**
We chose Option 3 (Hybrid). We will rely on tools like `pdfplumber` to detect headings (or extract PDF outlines). If text blocks are unmanageably large, we will fall back to sentence-aware chunking.

**Consequences:**
Ensures text sections sent to LLMs perfectly respect context boundaries, preventing prompt truncation or mid-sentence logic loss.

**Linked Requirements:** FR-003, FR-004

---

## ADR-002: Active LLM Model Cascading & Hard Failure

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
API rate limits, hallucinated unstructured outputs, and down-time can interrupt long batch evaluation scripts. 

**Decision:**
We will implement an active Model Cascading pattern (Claude -> Gemini). However, if both models fail on a segment (or encounter unrecoverable API errors), the system will intentionally *hard-fail*, printing the exact error and halting the pipeline, rather than recording `null` and continuing.

**Consequences:**
Prevents corrupted or partially-complete JSON files from being silently generated, ensuring data integrity at the cost of script restarts.

**Linked Requirements:** FR-006, NFR-003

---

## ADR-003: Standalone Metadata Extraction Workflow

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The roadmap explicitly decouples metadata extraction from evaluation. The CLI should natively fallback to a built-in extractor if necessary.

**Decision:**
The `Metadata Ingestor` component will blindly search the input directory for a `.json`, `.txt`, or `.html` file matching the PDF name. If found, it parses it. If not found, it invokes an internal `extract_metadata()` routine targeting the PDF text.

**Consequences:**
Maximizes flexibility, allowing precise manual metadata definition or easy automatic best-effort matching.

**Linked Requirements:** FR-001, FR-002

---

## ADR-004: Verbose JSON Output Strategy

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The format and size of the output JSON file define how easily the results can be audited. We had to choose between embedding the exact text of the segments evaluated, or just storing pointers/titles to save space.

**Decision:**
Embed the *full exact text* of every segment directly alongside its scores and LLM reasoning in the final JSON output.

**Consequences:**
Produces larger JSON files, but creates perfectly portable, self-contained artifacts that do not require the original PDF to be readable or auditable. This is critical for reproducible research datasets.

**Linked Requirements:** FR-007

---

## ADR-005: Prioritize Accuracy Over Speed for PDF Parsing

**Date:** 2026-02-22
**Status:** Accepted

**Context:**
The system needs to parse deeply structured educational PDFs. We initially considered `PyMuPDF` for its execution speed. The user explicitly intervened to specify that the project focus is entirely on accuracy and research properties, overriding efficiency concerns.

**Options Considered:**
1. *PyMuPDF:* Very fast, lightweight, but can struggle with complex tabular or visually-nested bounding boxes compared to others.
2. *pdfplumber:* Slower, heavier on memory, but heavily optimized for absolute positional accuracy, bounding box intersection, and exact character extraction.

**Decision:**
We will exclusively use `pdfplumber` (and drop `PyMuPDF` if they conflict) to maximize the deterministic accuracy of the text extraction and header detection.

**Consequences:**
Processing a large course PDF will take longer and consume more memory locally, but the extracted segments will be noticeably higher quality, directly supporting the research goals.

**Linked Requirements:** FR-003
