# Terminal Output Explanation

This document provides a detailed breakdown of the provided terminal output, explaining the commands executed, their successes/failures, and the meaning behind the specific steps and terminology (like "batch").

## 1. Metadata Extraction Command

### The Command
```bash
python3 -m src.metadata --pdf data/metadata/secondpart.pdf --output data/metadata/metadata1.json --ai --model gemini
```

### Purpose
This command runs a standalone metadata extraction script. It reads a given PDF file (`secondpart.pdf`), uses an AI model (`gemini`) to infer and extract course metadata (like title, author, description, prerequisites, etc.), and saves it as a JSON file (`metadata1.json`).

### Failure Situation
```text
2026-03-13 16:27:14,424 [ERROR] __main__: PDF file not found: data/metadata/secondpart.pdf
```
- **Explanation**: The first time the command was run, the system could not find the file `secondpart.pdf` at the specified path (`data/metadata/`). This usually happens due to a typo in the path, the file not being downloaded yet, or running the command from the wrong working directory. As a result, the script predictably aborted with an `[ERROR]`, preventing unnecessary API calls.

### Success Situation
The command is run again (presumably after placing the PDF in the correct path, or fixing the typo), and it succeeds:

1. **Fallback Logic**: 
   ```text
   2026-03-13 16:27:24,076 [INFO] __main__: No external metadata for 'secondpart'. Falling back to PDF.
   2026-03-13 16:27:24,328 [INFO] __main__: pdfplumber extracted unusually little text for 'secondpart.pdf'. Using pdftotext (Poppler) fallback.
   ```
   - The script first checks for existing metadata files. Finding none, it tries to read the PDF. The primary PDF text reader (`pdfplumber`) struggled to find text (this is very common with scanned, image-based, or heavily formatted PDFs). In response, it smartly fell back to an alternative layout-aware tool (`pdftotext`/Poppler library).
2. **Local Inference**: It successfully identifies basic information (author, description) locally from the raw text.
3. **AI Extraction**:
   ```text
   2026-03-13 16:27:24,980 [INFO] __main__: AI metadata: calling Gemini (attempt 1).
   ```
   - It sends the extracted text block to the Gemini API (`gemini-2.5-flash`) so the AI can contextually identify the specific educational metadata fields (like target audience or learning outcomes). 
4. **Summary and Export**:
   - The system displays a success summary box (`✓`) for fields it confidently identified and extracted, and warnings (`⚠`) for missing fields (like year or isbn). The final payload is successfully saved to the requested JSON path (`metadata1.json`).

---

## 2. Main Evaluation Pipeline Command

### The Command
```bash
python3 -m src.main --metadata data/metadata/metadata1.json --input data/courses --output data/output --config config/rubrics.yaml --model gemini
```

### Purpose
This is the core execution of the **SemanticFlow Pedagogical Evaluator**. It takes the previously extracted metadata file, scans the input directory for course PDFs, and evaluates/grades them against the specified rubrics in `config/rubrics.yaml` using the Gemini model.

### Output Breakdown & Step Explanations

- **Initialization**:
  It starts up, validates paths, loads the rubrics, and identifies that there is `1 PDF(s) to process` found inside the `data/courses/` folder.

- **Step 1/5: Metadata Extraction**
  Because the `--metadata` argument was provided, the pipeline skips raw text extraction and loads the pre-computed intelligence directly from `metadata1.json`.

- **Step 2/5: Deterministic Segmentation**
  Before passing an entire book or course to an LLM, the system "segments" the PDF. This means it breaks the long course document down into smaller, logical, and readable chunks based on its structure (e.g., chapters, modules, index pages).

- **Step 3/5: Module Gate Evaluation (12 segments)**
  This step evaluates the specific semantic parts of the content against the established educational rubrics (referred to as the "Module Gate"). The preceding segmentation step identified 12 distinct sections, so all 12 must be evaluated.

  **Explanation of "Batch"**:
  ```text
  2026-03-13 16:30:11,851 [INFO] SemanticFlowEvaluator:   [Module Gate] Batch 1/3 (5 segments)
  2026-03-13 16:30:46,327 [INFO] SemanticFlowEvaluator:   [Module Gate] Batch 2/3 (5 segments)
  2026-03-13 16:31:19,096 [INFO] SemanticFlowEvaluator:   [Module Gate] Batch 3/3 (2 segments)
  ```
  - **What does Batch mean?** A "batch" is a group of segments processed simultaneously or sequentially in one block. 
  - **Why Batch?** Instead of sending all 12 segments to the Gemini API at once (which would blow out maximum output token limits and confuse the model), or sending them individually (which would be extremely slow), the system chunks them together. Here, the internal batch size limit is `5`.
  - **Batch 1/3**: Evaluates segments 1 through 5.
  - **Batch 2/3**: Evaluates segments 6 through 10.
  - **Batch 3/3**: Evaluates the remaining segments 11 and 12.
  - Doing this ensures reliable context generation and avoids API threshold errors.

- **Step 4/5: Course Gate Evaluation (capstone)**
  After evaluating the granular pieces, the system "zooms out" to evaluate the course as a whole item. It verifies the overarching structure (e.g., is the sequence logical? How does the whole thing perform on high-level rubrics?). It combines structural awareness and the metadata to assign a finalized holistic score.

- **Step 5/5: Aggregation and Export**
  The system pulls all the modular metrics and Course Gate metrics together, computes a final structured report, and saves it into `data/output/secondpart_evaluation.json`.

- **Pipeline Completion**:
  `Evaluation pipeline completely finished.`
  The entire evaluation job was successfully resolved in just over 3 minutes (`took 3m6s`).
