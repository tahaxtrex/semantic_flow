# Critical Feedback on Assessment Quality (Semantic Flow)

This document records the faults identified in the assessment logic and implementation of the Semantic Flow project, based on an analysis of the `course2_evaluation.json` output and the source code in the `src/` folder.

## Identified Faults & Suggested Fixes

### 1. Broken Cross-Batch Repetition Detection (ADR-030)
*   **Fault**: The `main.py` script fails to pass the `previous_summaries` list to the `evaluator.evaluate_batch` method. As a result, the cross-segment context injection intended to detect repetition across different batches (ADR-030) is completely neutralized. The LLM only sees the current 5 segments and has no awareness of the narrative so far.
*   **Impact**: Redundant content across the course is not penalized if it spans across different batches of 5 segments.
*   **Suggested Fix**: Update the loop in `src/main.py` to maintain a list of summaries and pass them to the evaluator.
    ```python
    # In src/main.py
    previous_summaries = []
    for i in range(0, len(segments), BATCH_SIZE):
        batch = segments[i:i + BATCH_SIZE]
        eval_batch = evaluator.evaluate_batch(metadata, batch, previous_summaries=previous_summaries)
        # Extract summaries from eval_batch and append to previous_summaries
        previous_summaries.extend([s.summary for s in eval_batch if s.summary])
        evaluated_segments.extend(eval_batch)
    ```

### 2. Mathematical Formula Fragmentation
*   **Fault**: `src/segmenter.py` groups words into lines using a strict 2pt vertical tolerance (`round(w['doctop'] / 2) * 2`). This is insufficient for mathematical notation where exponents, subscripts, and summation symbols naturally sit on different vertical baselines.
*   **Impact**: Formulas like $\sigma^2 = \frac{\sum(x_i - \mu)^2}{n}$ are extracted as fragmented, multi-line gibberish. The LLM then penalizes the course for "broken rendering" or "missing symbols," which are actually artifacts of the segmenter.
*   **Suggested Fix**: 
    1.  Increase vertical tolerance for baseline grouping (e.g., 4-5pt).
    2.  Implement a "formula-aware" grouping logic that detects symbols like `Σ`, `∫`, or superscript/subscript positions and keeps them in the same logical block.
    3.  Consider using `pdfplumber`'s `horizontal_ltr` or `vertical_ttb` grouping strategies if they prove more robust for math.

### 3. Rubric Redundancy (Readability vs. Clarity)
*   **Fault**: The `text_readability` and `pedagogical_clarity` rubrics in `config/rubrics.yaml` both include criteria that overlap when it comes to technical notation and symbolic errors.
*   **Impact**: A single extraction error (like the fragmented formula) results in double-penalization across two different rubrics, artificially deflating the overall score.
*   **Suggested Fix**: Clearly demarcate the rubrics:
    *   **Readability**: Focus on grammar, spelling, sentence length, and prose flow.
    *   **Pedagogical Clarity**: Focus on terminology definitions, prerequisite ordering, and logical structure. 
    *   **New Rubric (Technical Precision)**: Move formula/notation accuracy to a dedicated rubric if math is a primary target.

### 4. Counter-Intuitive Scoring Weight (Aggregator)
*   **Fault**: `src/aggregator.py` weights module scores by character length (`len(s.text)`). 
*   **Impact**: Segments that are overly verbose or filled with "fluff" contribute more to the final course score than concise, high-impact segments. This contradicts the `goal_focus` rubric which explicitly penalizes digressions.
*   **Suggested Fix**: 
    1.  Use a simple average if segments are of comparable importance.
    2.  Alternatively, use the `goal_focus` score itself to *reduce* the weight of "fluffy" segments.
    3.  Introduce a "complexity" or "importance" flag in the segmenter or evaluator.

### 5. Rationale Truncation & Repetition
*   **Fault**: In `src/aggregator.py`, the `_build_assessment_tree` method truncates rationales to 150-220 characters and combines them with pipe separators.
*   **Impact**: The most critical evidence for a low score (the "Weakest" rationale) is often cut off mid-sentence, making the feedback less actionable. Additionally, since the same rationale might be used for multiple segments in a batch, the summary feels repetitive.
*   **Suggested Fix**: 
    1.  Allow longer rationales in the `AssessmentTree` (the JSON output can handle it).
    2.  Instead of joining Weakest/Typical/Strongest, have the LLM produce a dedicated "Consolidated Rationale" for the entire dimension at the end of the batch evaluation.

### 6. Instruction Leakage / Artifact Penalization
*   **Fault**: Despite the system prompt instructing the LLM to ignore "minor formatting artifacts," the LLM consistently penalizes fragmented formulas and PDF placeholders.
*   **Impact**: The assessment quality is compromised because it measures the tool's extraction quality rather than the course's pedagogical quality.
*   **Suggested Fix**: 
    1.  Strengthen the "Extraction Artifacts" warning in the system prompt.
    2.  Show the LLM examples of "broken text" that it should explicitly ignore.
    3.  Improve the segmenter (Fix #2) to reduce the number of artifacts the LLM sees.

### 7. Gemini Model Name Hardcoding
*   **Fault**: `src/evaluator.py` hardcodes `self.gemini_model = 'gemini-2.5-flash'`.
*   **Impact**: If this model version is not available in the current environment (e.g., if the user meant `gemini-1.5-flash`), the script will fail immediately.
*   **Suggested Fix**: Make the model name configurable via the constructor or a configuration file, defaulting to a known stable version.
