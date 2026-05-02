"""Provider Protocol shared by every KAM extractor.

A provider must accept educational text plus optional context hints and return
a dict that hydrates `vendor.kam.schema.ExtractionOutput` via `model_validate`.
"""

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ExtractionProvider(Protocol):
    model: str

    def extract(
        self,
        text: str,
        domain_hint: Optional[str] = None,
        course_title: Optional[str] = None,
        chapter_title: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict: ...

    def estimate_cost(self, text: str) -> dict: ...
