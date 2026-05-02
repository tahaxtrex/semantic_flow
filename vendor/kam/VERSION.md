# Vendored KAM Snapshot

This directory is a copy-vendored snapshot of the extraction layer from the
sister project `semantic_kam`. It is not a git submodule; treat the files
here as part of `semantic_flow` and modify them only via the patches
documented below. To pull a new upstream snapshot, follow the sync
instructions at the bottom.

## Source

| Field | Value |
|---|---|
| Upstream repo path | `../../semantic_kam` (sibling clone of `semantic_flow`) |
| Upstream subdirectory | `project1/` |
| Upstream commit SHA | `0cb035db1d8723f1daf91701c1f75a1b7e870268` |
| Sync date | 2026-05-02 |
| Synced by | semantic_flow integration v1 (KAM adapter) |

## Files in this snapshot

| Local path | Upstream path | Patches applied |
|---|---|---|
| `schema.py` | `project1/extraction_engine/schema.py` | none (verbatim) |
| `chunker.py` | `project1/extraction_engine/chunker.py` | none (verbatim) |
| `prompts.py` | `project1/extraction_engine/prompts.py` | none (verbatim) |
| `providers/anthropic.py` | `project1/llm_providers/anthropic_provider.py` | (1) `from extraction_engine.prompts` → `from ..prompts`; (2) optional `client` injection; (3) `print` → `logging` |
| `providers/openai.py` | `project1/llm_providers/openai_provider.py` | same three patches as anthropic.py |
| `providers/gemini.py` | NEW (no upstream) | written for v1; mirrors `semantic_flow/src/evaluator.py:73,491-499` Gemini pattern |
| `providers/base.py` | NEW (no upstream) | `ExtractionProvider` Protocol shared by all providers |

## Files explicitly NOT vendored

These exist upstream but are excluded for the reasons listed (see plan
`/home/xtrex/.claude/plans/can-you-read-the-linear-coral.md` for full
rationale):

- `project1/run_extraction.py` — CLI orchestrator with hard `config/.env` load and `sys.path.insert`. Reimplemented in `semantic_flow/src/kam/extraction.py`.
- `project1/validate_full_pipeline.py` — writes `outputs/verification_report.json`; conflicts with our Finding model.
- `project1/abstraction_layer.py` — silently mutates the graph to break cycles, violating spec §0.5.
- `project1/reasoning_layer.py`, `topic_layer.py`, `hierarchy_layer.py` — out of v1 scope (V-COH dropped, V-ABS deferred).
- `project1/integrity_layer.py`, `prerequisite_completion.py`, `reasoning_validator.py` — coupled to validate_full_pipeline; v1 implements its own validators.
- `project1/llm_providers/kie_ai_provider.py`, `gemini_image_provider.py` — out of scope; v1 only needs text extraction.

## Sync instructions (manual)

To pull a newer KAM snapshot:

```bash
# from semantic_flow repo root
KAM_REPO=../semantic_kam
NEW_SHA=$(git -C "$KAM_REPO" rev-parse HEAD)

# overwrite vendored files
cp "$KAM_REPO"/project1/extraction_engine/schema.py vendor/kam/schema.py
cp "$KAM_REPO"/project1/extraction_engine/chunker.py vendor/kam/chunker.py
cp "$KAM_REPO"/project1/extraction_engine/prompts.py vendor/kam/prompts.py
cp "$KAM_REPO"/project1/llm_providers/anthropic_provider.py vendor/kam/providers/anthropic.py
cp "$KAM_REPO"/project1/llm_providers/openai_provider.py   vendor/kam/providers/openai.py

# re-apply patches in providers/anthropic.py and providers/openai.py:
#   1. `from extraction_engine.prompts` → `from ..prompts`
#   2. add optional `client: Optional[<ClientType>] = None` __init__ argument
#   3. replace print(...) with logging.getLogger(__name__).info(...)

# update this file: bump SHA + sync date

# run the smoke test
pytest tests/test_kam/test_vendor_imports.py -v
pytest tests/test_kam/ -v
```

If the upstream schema or prompt changes break our adapter or invalidate the
`data/cache/kam/` extraction cache, bump the cache key's `prompt_version`
constant in `semantic_flow/src/kam/extraction_cache.py` and discard the cache
directory.

## Known technical debt: orphan cache files on version bumps

The extraction cache key includes `PROMPT_VERSION` and `SCHEMA_VERSION` from
`src/kam/extraction_cache.py`. When either constant is bumped, every previously
written cache file at `data/cache/kam/**/*.json` becomes unreachable — the new
keys never collide with old ones, so reads always miss and re-extract. The old
files stay on disk forever unless someone deletes them.

**Mitigation (deferred):** the cache layer needs a manual purge command, e.g.
`python -m src.kam.extraction_cache --purge-orphans` that walks the directory,
opens each entry, reads its `metadata.prompt_version` / `schema_version`, and
deletes anything not matching the live constants. Until that ships, operators
should run `rm -rf data/cache/kam/` after bumping either version constant.

This is logged here (not in a TODO file) so anyone syncing a new KAM snapshot
sees it before they touch the version constants.
