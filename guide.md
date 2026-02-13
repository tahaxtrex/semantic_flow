# SemanticFlow Evaluator: User & Operator Guide

## 1. Setup

### Installation
```bash
pip install -r requirements.txt
```

### Environment Variables
Create a `.env` file in the root directory:
```env
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
```

---

## 2. Running the Tool

### Step 1: Extract Metadata (Optional)
If your PDF metadata is insufficient, use the `metadata_tool` to fetch it from a URL or JSON:
```bash
# From URL
python3 -m src.metadata_tool --url "https://course-page.com" --output data/metadata/course_a.json


# From manual JSON template
python3 -m src.metadata_tool --json templates/my_meta.json --output data/metadata/course_a.json
```

### Step 2: Run Evaluation
Pass the metadata file to the evaluator using the `--meta` flag:
```bash
python3 -m src.cli --course data/courses/course_a.pdf --meta data/metadata/course_a.json
```

---

## 3. Configuration & Tuning

### Modifying the Rubrics
If you want to change how "Pedagogical Clarity" is evaluated, edit `config/rubrics.yaml`:
```yaml
pedagogical_clarity:
  description: "Your new custom description here..."
```

### Changing Segmentation Rules
To adjust the 20-page limit or add new regex markers (e.g., for "Lecture X"), edit `config/settings.yaml`:
```yaml
segmentation:
  max_pages_per_segment: 15
  regex_patterns:
    - "^Lecture \d+"
```

---

## 4. Understanding Outputs

### Segment Evaluations
Found in `data/evaluations/`. Each file contains the scores for one chapter:
```json
{
  "scores": { "goal_focus": 8, ... },
  "overall_observations": "Reasoning here..."
}
```

### Final Reports
Found in `data/reports/`. This contains the **Simple Average** of all segment scores for that course.

---

## 5. Troubleshooting

- **LLM Failure**: If Claude fails, check the console for "Falling back to Gemini". If both fail, verify your API keys and internet connection.
- **Empty Segments**: If a PDF is just images (scanned), `pdfplumber` will return empty text. You may need to run OCR on the PDF first.
- **Malformed TOC**: If a PDF has a broken Table of Contents, the segmenter will fall back to Regex. You can see the segmentation results in `data/segments/`.
