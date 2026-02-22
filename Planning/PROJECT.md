# SemanticFlow Pedagogical Evaluator

## Elevator Pitch
A deterministic, low-cost Python CLI tool that segments educational content and uses LLMs as structured evaluators to assess pedagogical quality based on defined rubrics.

## Motivation
Generative quality in LLMs has improved, but evaluating the pedagogical soundness of educational content (e.g., lecture notes, course modules) is underdeveloped. We need a system to ensure content goes beyond linguistic fluency to provide instructional coherence and structural integrity.

## Goals
1. Provide a reproducible LLM-based assessment pipeline for educational materials.
2. Segment content accurately and deterministically (without LLMs) to minimize costs.
3. Evaluate content across eight specific pedagogical dimensions using structured prompts.
4. Output structured JSON results and aggregate scores at the course level.
5. Track and log all architectural and prompt version changes.

## Non-Goals
- Not a web application or API service.
- No database system (JSON storage only).
- Not a content generator or recommendation engine.
- No LLM-based segmentation.
- Not an autonomous system or real-time API.

## Target Users
Researchers and educators involved in the SemanticFlow project who need a reproducible, empirical way to evaluate the pedagogical quality of educational text.

## Success Criteria
- Runs fully via CLI.
- Automatically processes course materials from a designated folder.
- Extracts metadata from PDFs, JSON, or HTML.
- Outputs reliable, validated JSON evaluations.
- Minimizes LLM cost (e.g., retries only on invalid JSON).
- Logs all structural decisions and prompt changes transparently.

## Constraints
- **Technical:** Python 3.12+, argparse CLI, JSON storage.
- **Cost:** Controlled temperature, deterministic segmentation only.
