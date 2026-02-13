import os
import yaml
import instructor
from typing import Optional, List
from anthropic import Anthropic
import google.generativeai as genai
from dotenv import load_dotenv
from src.models import EvaluationResult, CourseMetadata

load_dotenv()

class PedagogicalEvaluator:
    def __init__(self, rubric_path: str = "config/rubrics.yaml"):
        with open(rubric_path, 'r') as f:
            self.rubrics = yaml.safe_load(f)
        
        # Initialize clients
        self.anthropic_client = instructor.from_anthropic(Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")))
        
        # Backup: Gemini
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.gemini_client = instructor.from_gemini(
            client=genai.GenerativeModel(model_name="models/gemini-flash-latest"),
            mode=instructor.Mode.GEMINI_JSON,
        )

    def _generate_system_prompt(self, metadata: Optional[CourseMetadata] = None) -> str:
        prompt = "You are an expert pedagogical auditor. Your task is to evaluate the following educational text segment based on 8 specific rubrics.\n\n"
        
        if metadata:
            prompt += "### GLOBAL COURSE CONTEXT:\n"
            prompt += f"- **Course Title**: {metadata.title}\n"
            prompt += f"- **Author**: {metadata.author}\n"
            prompt += f"- **Description**: {metadata.description}\n"
            prompt += f"- **Learning Outcomes**: {', '.join(metadata.learning_outcomes) if metadata.learning_outcomes else 'Not specified'}\n"
            prompt += f"- **Prerequisites**: {', '.join(metadata.prerequisites) if metadata.prerequisites else 'None specified'}\n\n"
            prompt += "Use this global context to judge if the current segment aligns with the intended learning goals and prerequisite levels.\n\n"

        prompt += "### Rubric Definitions:\n"
        for name, data in self.rubrics.items():
            clean_name = name.replace("_", " ").title()
            prompt += f"- **{clean_name}**: {data['description']}\n"
        
        prompt += "\n### Scoring Guidelines:\n"
        prompt += "- Provide an integer score from 1 to 10 for each rubric.\n"
        prompt += "- 1: Fails completely; 10: Perfect pedagogical implementation.\n"
        prompt += "- Be objective and critical. Your evaluation is for research purposes.\n"
        return prompt

    def evaluate_segment(self, segment_text: str, segment_title: str, metadata: Optional[CourseMetadata] = None) -> EvaluationResult:
        system_prompt = self._generate_system_prompt(metadata)
        user_content = f"Segment Title: {segment_title}\n\nContent:\n{segment_text[:50000]}" # Truncate if extreme, though 20 pages usually fits.

        try:
            # Primary: Claude 3.5 Sonnet
            response = self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                response_model=EvaluationResult,
            )
            return response
        except Exception as e:
            print(f"Claude failed, falling back to Gemini... Error: {e}")
            # Fallback: Gemini
            try:
                response = self.gemini_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    response_model=EvaluationResult,
                )
                return response
            except Exception as e2:
                raise RuntimeError(f"Both LLM providers failed: {e2}")
