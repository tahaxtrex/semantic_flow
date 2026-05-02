"""Vendored snapshot of semantic_kam extraction layer.

Source repo: https://github.com/<owner>/semantic_kam (sibling clone at ../../semantic_kam)
Snapshot SHA, sync date, and per-file provenance: see VERSION.md.

Public API:
    from vendor.kam import schema, chunker, prompts
    from vendor.kam.providers import GeminiProvider, AnthropicProvider, OpenAIProvider
"""

from . import chunker, prompts, schema

__all__ = ["chunker", "prompts", "schema"]
