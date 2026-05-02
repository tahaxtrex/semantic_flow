# Claude Code Initialization Context: SemanticFlow + Semantic KAM Integration

## Purpose of this Document
This file contains the "full picture" for an upcoming architectural integration. You (Claude) will be leading the implementation. This document provides the theoretical goals, the current state of both repositories, and the desired outcome. 

**Do not follow a pre-written plan. You are the architect. Read this context, explore the codebases, and formulate your own implementation plan.**

---

## 1. The High-Level Goal: Neuro-Symbolic Pedagogical Evaluation
Currently, the `semantic_flow` project evaluates the pedagogical quality of textbooks using a purely subjective LLM pipeline (`evaluator.py`). The LLM reads a chapter and tries to guess if the cognitive load is too high or if prerequisites are missing.

The goal is to transition `semantic_flow` from an "LLM Opinion Generator" into a "Mathematical Proof Engine." 

We will achieve this by importing the graph-extraction workflow from the `semantic_kam` project. `semantic_kam` parses text and extracts a strict Directed Acyclic Graph (DAG) of Concepts, Prerequisites, and Bridges. By running the text through KAM *before* the LLM evaluator, we can mathematically prove structural errors (e.g., a cycle, a density spike, a missing bridge) and feed those objective facts into the LLM prompt to ground its scoring.

---

## 2. The Repositories Involved
Both projects live in the parent directory: `/home/xtrex/Documents/project/github/`

### A. The Target Repository (Where you will be working)
**Path:** `semantic_flow/`
**Role:** The Educational Auditor.
**Key Files:**
*   `src/segmenter.py`: Chunks the PDF into logical chapters. (Keep this).
*   `src/evaluator.py`: The LLM evaluator that we need to modify.
*   `config/rubrics.yaml`: The strict 0/1/2 grading rubrics.
*   `spec/structural_model_master_doc_v2_1.md`: The "Master Spec" defining the theory behind all of this (Learner Profiles, Cognitive Load Index).

### B. The Source Repository (What we are integrating)
**Path:** `semantic_kam/project1/`
**Role:** The Knowledge Graph Extractor.
**Key Files to study:**
*   `run_extraction.py`: The LLM logic that pulls Concepts and Relations from text.
*   `abstraction_layer.py`: Computes absolute abstraction levels (L1-L5) and contains the cycle-breaking logic.
*   `validate_full_pipeline.py`: Enforces referential integrity (catches broken prerequisite links).

---

## 3. The Core Integration Concepts

You need to architect a pipeline that behaves as follows:

1.  **Segment:** `semantic_flow` chunks the text into a Module.
2.  **Extract (The KAM step):** Run that text through KAM's extraction logic to build a JSON graph of Concepts and Prerequisite Edges.
3.  **Deterministic Quality Gates:** Run Python math on the extracted JSON to prove errors:
    *   *Density Proxy (Intrinsic Load):* `Count(Concepts) / Count(Sentences)`. If too high, flag `LOAD-003`.
    *   *Sequencing Error:* If the JSON says `A -> B`, but `B` appears before `A` in the text, flag `STR-002`.
    *   *Cycle Detection:* Intercept KAM's `_break_cycles` function. If it fires, don't just fix the graph—emit a `STR-001` structural penalty against the course.
4.  **The Grounded LLM Prompt:** Modify `semantic_flow/src/evaluator.py`. Do not just send the raw text. Send the raw text + a deterministic summary of the KAM graph (e.g., "Math proves 14 concepts, 0 bridges, 1 cycle"). Force the LLM to obey this reality when scoring the rubrics.

---

## 4. Deep Dive Documentation Available
Before you start planning the code, you **must** read the following synthesis documents located in the root `/home/xtrex/Documents/project/github/` folder. They contain the exact breakdown of how KAM's math maps to SemanticFlow's rubrics:

1.  `project_analysis_and_synergy.md`: A detailed architectural breakdown of both projects and where they align/diverge.
2.  `module_gate_kam_integration_ideologue.md`: The specific ideas for translating KAM's graph math into rubric penalties.
3.  `integration_q_and_a.md`: Answers to edge cases (e.g., how to handle multi-topic "survey" courses with multiple disconnected pyramids using Leiden clustering).

---

## 5. Your Mission

You are currently on the `kam/module` branch inside `semantic_flow`.

Your task is to:
1. Absorb this context and the linked markdown files.
2. Explore the `semantic_flow` and `semantic_kam` codebases to understand the current data structures.
3. Propose a concrete implementation plan for how to cleanly inject the KAM extraction and graph validation logic into the `semantic_flow` pipeline without destroying the existing two-gate architecture.
4. Await user approval before writing code.
