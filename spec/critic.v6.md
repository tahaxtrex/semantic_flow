# Critic Report v6 — The Transition to Neuro-Symbolic Production Architecture

## 1. The Context: Transitioning from v1 Prototype to v2 Research Tool

The v1 integration of `semantic_kam` into `semantic_flow` successfully established a "Neuro-Symbolic Pipeline." By extracting a mathematical graph of concepts from educational text, it grounded the subjective LLM evaluation in deterministic facts (Density, Cycles, Sequencing).

However, the v1 architecture relies on **binary error flags** (e.g., `LOAD-003 detected`) and **Pydantic retry loops** (forcing the LLM to rewrite its evaluation if it disobeys a score cap). While effective for a prototype, this approach presents three major scalability risks for production or research usage:
1.  **Crude Penalty Application:** A textbook introducing 1.05 concepts per sentence triggers the same binary error flag as a textbook introducing 2.40 concepts per sentence.
2.  **Infinite Token Burning:** A stubborn LLM that refuses to obey a mathematical score cap triggers a retry loop, burning expensive tokens (e.g., Claude 4.6 Sonnet) simply to fix a number.
3.  **Loss of Research Data:** By forcing the LLM to "fix" its hallucinated high score before saving the JSON, the system destroys the evidence that the LLM hallucinated in the first place.

This document outlines the v2 architectural blueprint to solve these issues, transitioning from an enforcement paradigm to a **deterministic clamping paradigm**.

---

## 2. Core Architectural Shifts (v2 Blueprint)

### A. Granular Severity over Binary Flags
Validators must stop returning simple boolean triggers. Every mathematical violation must return a **calculated severity score** on a continuous scale (e.g., 0.0 to 10.0).

**Example: V-DENS (Density)**
Instead of: `if density > 1.0: emit error`
Implement: `severity = min(10, ((density - 1.0) / 1.0) * 10)`
*   `density = 1.1` → Severity 1 (Minor phrasing issue)
*   `density = 1.5` → Severity 5 (Noticeable cognitive load spike)
*   `density = 2.0` → Severity 10 (Pedagogically chaotic)

**Example: V-FWD (Sequencing)**
Instead of flagging any inverted edge, track the magnitude of the inversion:
*   Inverted by 1 sentence → Mild severity.
*   Inverted by 6 pages (`max_distance_chars = 12000`) → Critical severity.

**The Output Schema (v2 Finding):**
```json
{
  "code": "LOAD-003",
  "title": "Concept density spike",
  "severity": 7.8,
  "confidence": 0.91,
  "message": "The segment introduces 82 concepts across 45 sentences.",
  "raw_metrics": {
    "concept_count": 82,
    "sentence_count": 45,
    "concept_density": 1.82
  },
  "affected_rubrics": {
    "goal_focus": 5,
    "pedagogical_clarity": 6
  }
}
```

---

### B. The "Python Clamp" (O(1) Score Enforcement)
The Pydantic `@model_validator` loop that rejects LLM payloads for violating score caps is dangerous at scale. The new strategy is to **never rely on the LLM to obey mathematical constraints post-generation.**

**The v2 Workflow:**
1.  **Extract:** KAM extracts the graph.
2.  **Evaluate:** The LLM evaluates the prose and assigns its own scores, guided by the severity metrics in the prompt.
3.  **Validate:** Pydantic ensures the JSON format is correct. If the JSON format is broken, retry the LLM (Format Repair Only).
4.  **Clamp (Python):** Python automatically clamps the LLM's returned score against the deterministic cap.
    *   `final_score = min(claude_score, deterministic_cap)`

By moving score enforcement out of the LLM prompt and into native Python math, you guarantee O(1) execution time and eliminate the risk of a $100 token burn on a single paragraph.

---

### C. Capturing the "Hallucination Delta" (Research Value)
To turn `semantic_flow` into a publishable research tool, the system must record the difference between what the LLM *thought* the score should be and what the math *proved* it should be.

**The v2 Output Structure:**
```json
{
  "rubric": "goal_focus",
  "llm_score": 9,
  "final_score": 5,
  "adjusted": true,
  "adjustment_reason": "LOAD-003 severity 7.8 clamped goal_focus at 5."
}
```
*Why this matters:* This data structure allows researchers to query the output and state, *"In 10,000 evaluations, Claude 4.6 Sonnet overrated structurally broken modules 42% of the time, proving the necessity of the deterministic KAM layer."*

---

## 3. The "Override Acknowledgment" (Prompt Tuning)

If Python silently clamps an LLM score from a 9 to a 5, the LLM's generated `reasoning` paragraph (which likely praises the chapter) will severely contradict the final 5/10 score.

To fix this cognitive dissonance without resorting to retry loops, the system prompt must explicitly instruct the LLM on how to handle caps in its reasoning.

**The v2 Prompt Injection:**
> *"You must evaluate the prose quality as you see it. However, if your intended score exceeds the mathematical cap provided in `<KAM_SCORE_CONSTRAINTS>`, you must limit your final score to that cap, AND you MUST explicitly state in your reasoning paragraph: 'While the prose is clear, I have capped the score at [X] due to the severe [Y] detected by the deterministic math.'"*

---

## 4. KAM Extraction Failsafe

If the initial Gemini KAM extraction fails (e.g., due to a Pydantic `ValidationError` on a malformed graph schema), the entire pipeline must not crash. 

**The v2 Retry Policy:**
*   **Graph Extraction (Gemini):** Allow 1-2 retries with a repair prompt (since Gemini Flash is extremely cheap). If it still fails, return a degraded report (`"kam_status": "failed"`) and proceed to the Claude evaluation without grounding. The final report must explicitly note: *"KAM graph unavailable for this segment. Scores rely only on LLM judgment."*
*   **LLM Evaluation (Claude):** Retry ONLY for broken JSON syntax. NEVER retry for score cap violations (handled by Python Clamping).

---

## Summary
The transition from v1 to v2 marks a shift from **"LLM coercion"** to **"Deterministic overlay."** By using continuous severity metrics, Python-side score clamping, and explicit tracking of LLM vs. Math scores, `semantic_flow` becomes cheaper, safer, and infinitely more valuable as an educational research instrument.