## `1. Background and Rationale`

`Large language models (LLMs) are increasingly used to generate structured educational content, including lecture notes, summaries, course modules, and explanatory materials. While generative quality has improved significantly, systematic methods for evaluating **pedagogical soundness** remain underdeveloped.`

`In the context of the **SemanticFlow system**, where structured semantic representations are extracted, transformed, and potentially regenerated, ensuring the pedagogical quality of both reference and generated content is critical. Semantic correctness alone is insufficient; content must also demonstrate instructional coherence, clarity, and structural integrity.`

`This project investigates whether LLMs can be used not only as generators but as **structured evaluators of pedagogical quality**.`

---

## `2. Problem Statement`

`Existing evaluation practices for LLM-generated content focus largely on:`

- `Linguistic fluency`
- `Semantic similarity`
- `Task completion accuracy`
- `Perplexity or token-level metrics`

`However, educational quality depends on dimensions that go beyond surface-level fluency. These include structural progression, prerequisite alignment, and example coherence — properties essential for effective learning.`

`The central research problem is:`

> `Can large language models reliably assess pedagogical quality across structured educational dimensions, and under what conditions are these assessments valid?`
> 

---

## `3. Pedagogical Quality Dimensions (Evaluation Rubrics)`

`This project operationalizes pedagogical quality using eight structured rubrics. These rubrics define the evaluation framework and are treated as measurable dimensions rather than subjective impressions.`
 
 ## `4. Pedagogic dimentios`
`rubrics are available in the rubrics.yaml file`

## `4. Research Objectives`

`The objectives of this project are:`

1. `To design a structured framework for evaluating educational materials across the eight pedagogical dimensions defined above.`
2. `To implement a deterministic evaluation pipeline using LLMs as structured assessors.`
3. `To measure how prompting strategies affect assessment reliability.`
4. `To evaluate the correlation between LLM-based scores and expert human judgments.`
5. `To identify systematic biases and failure modes in LLM-based pedagogical evaluation.`

---

## `5. Methodological Approach`

`The project adopts a reproducible and structured methodology:`

- `run a script to get the matadata and store it in a json format`
- `Educational content is segmented deterministically (non-LLM segmentation).`
- `LLMs assess each section using structured prompts.`
- `Evaluations are returned in strict JSON format.`
- `Aggregation occurs at both section and course levels.`
- `Prompting strategies (zero-shot, few-shot, chain-of-thought) are later compared.`
- `A human evaluation subset is used for correlation analysis (to be done).`

`The evaluation framework is version-controlled and reproducible.`

---

## `6. Research Questions`

1. `How do different prompting strategies (zero-shot, few-shot, chain-of-thought) affect assessment stability and accuracy?`
2. `Which of the eight pedagogical dimensions can LLMs assess reliably?`
3. `Can LLMs identify specific quality issues such as missing prerequisites, logical gaps, or unclear explanations?`
4. `How strongly do LLM-based scores correlate with expert human evaluations?`
5. `What systematic failure modes or biases emerge in LLM pedagogical assessment?`

---

## `7. Expected Contributions`

`This project contributes:`

- `A formalized rubric-based pedagogical evaluation framework.`
- `A reproducible LLM-based assessment pipeline.`
- `Empirical analysis of prompting strategy effects.`
- `Correlation analysis between automated and human evaluation.`
- `Identification of bias and reliability limitations.`
- `A structured quality metric suite for integration into SemanticFlow WP1.`
