## `1. Core Objective`

`Build a deterministic, low-cost, CLI-based evaluation pipeline that:`

- `Reads course PDFs from a project folder`
- `Segments content accurately (non-LLM) using a smart way rather than naive way of number of caracters, can use smart python lmibraries`
- `Evaluates pedagogical quality using structured LLM prompts`
- `Outputs structured JSON results`
- `Tracks all changes and decisions`
- `Remains reproducible and transparent`

---

## `2. What This Project IS`

- `A local Python CLI tool`
- `Deterministic segmentation (no LLM-based parsing)`
- `JSON-only storage (no database)`
- `Folder-based input/output structure`
- `Research-oriented and reproducible`
- `Cost-aware (minimal LLM usage)`
- `Prompt-version controlled`
- `Transparent logging via log.md`
- `Accuracy-focused (validation + consistency checks)`
- `modular, can extract metadata from pdf, json, or html`

---

## `3. What This Project IS NOT`

- `Not a web application`
- `Not a database system`
- `Not a recommendation engine`
- `Not a content generator`
- `Not an autonomous system`
- `Not a real-time API service`
- `Not over-engineered`
- `Not cloud-dependent`

---

## `4. Technical Constraints`

- `Language: Python 3.12+`
- `Interface: CLI via argparse`
- `Storage: JSON files only`
- `Logging: log.md`
- `Deterministic segmentation only`
- `Strict JSON schema for LLM output`

---

## `5. Cost & Efficiency Constraints`

- `No unnecessary LLM calls`
- `No LLM-based segmentation`
- `Batch section evaluation`
- `Reuse evaluation if already exists`
- `Controlled temperature`
- `Retry only on invalid JSON`

---

## `6. Folder Structure`

- `should be well structured and organized and easily readable, the project be very simple`

---

## `7. Execution Flow`

1. `Read PDFs from /data/courses`
2. `Extract metadata (separate script)`
3. `Perform deterministic segmentation`
4. `Save sections to JSON`
5. `Evaluate sections via LLM with metadata consideration`
6. `Validate JSON output`
7. `Aggregate course-level scores`
8. `Save final results in /data/output`
9. `Log changes in log.md`

---

## `8. Accuracy Requirements`

- `Segmentation must respect structural boundaries`
- `JSON schema must always validate`
- `LLM output must be reproducible`
- `Aggregation must be mathematically correct`
- `Prompt version must be recorded`
- `No overwriting without version increment`

---

## `9. Logging Policy`

`All major events recorded in log.md:`

- `Architecture changes`
- `Prompt updates`
- `Segmentation rule changes`
- `Schema changes`
- `Experiment runs`
- `Error events`

`Each entry must include:`

- `Date`
- `Version`
- `Summary of change`
- `Rationale`

---

## `10. Versioning`

`have an reseonable versionning and have a task.md file that include all tasks to be made`

---

## `11. Minimal Milestones`

### `M1 — Deterministic Segmentation Stable`

### `M2 — JSON Evaluation Pipeline Working`

### `M3 — Aggregation Accurate`

### `M4 — Prompt Strategy Comparison`

### `M5 — Human Correlation Study`

---

## `12. Success Criteria`

`The system is successful if:`

- `It runs entirely from CLI`
- `It processes courses from folder automatically`
- `It produces structured JSON outputs`
- `It read meta data from all 3 sources possible`
- `It minimizes LLM cost`
- `It maintains reproducibility`
- `It logs all structural decisions`
- `It achieves stable evaluation outputs`
