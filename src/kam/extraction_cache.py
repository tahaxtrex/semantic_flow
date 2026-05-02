"""Disk-backed extraction cache for the KAM adapter.

Cache layout (under `data/cache/kam/` by default):

    data/cache/kam/
      <first_two_chars_of_key>/
        <full_key>.json              # cache entry
        <full_key>.json.tmp          # partial write — ignored by reads

Cache key: sha256 of a canonicalized JSON payload containing
  - segment text (verbatim),
  - extractor model id,
  - PROMPT_VERSION (bumped when prompts.MASTER_EXTRACTION_PROMPT changes),
  - SCHEMA_VERSION (bumped when schema.ExtractionOutput changes).

Bumping either constant invalidates the entire cache without needing to
delete files manually — old keys simply never match again.

Cache entries are JSON {"payload": <ExtractionOutput dict>, "metadata":
{...}}. On read the file is parsed; if JSON parsing fails the file is
quarantined to <key>.json.corrupt and the cache reports a miss so the
caller can re-extract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0.0"
SCHEMA_VERSION = "v1.0.0"

DEFAULT_CACHE_DIR = Path("data/cache/kam")


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    corrupt_quarantined: int = 0


@dataclass
class CacheEntry:
    payload: dict
    metadata: dict = field(default_factory=dict)


class ExtractionCache:
    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        *,
        enabled: bool = True,
        prompt_version: str = PROMPT_VERSION,
        schema_version: str = SCHEMA_VERSION,
    ):
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self.stats = CacheStats()
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def make_key(self, *, segment_text: str, extractor_model: str) -> str:
        payload = json.dumps(
            {
                "segment_text": segment_text,
                "extractor_model": extractor_model,
                "prompt_version": self.prompt_version,
                "schema_version": self.schema_version,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path_for_key(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[CacheEntry]:
        if not self.enabled:
            self.stats.misses += 1
            return None
        path = self._path_for_key(key)
        if not path.exists():
            self.stats.misses += 1
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Quarantining corrupt cache entry %s: %s", path, e)
            try:
                path.rename(path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            self.stats.corrupt_quarantined += 1
            self.stats.misses += 1
            return None

        if not isinstance(raw, dict) or "payload" not in raw:
            logger.warning("Cache entry %s missing 'payload'; quarantining", path)
            try:
                path.rename(path.with_suffix(".json.corrupt"))
            except OSError:
                pass
            self.stats.corrupt_quarantined += 1
            self.stats.misses += 1
            return None

        self.stats.hits += 1
        return CacheEntry(payload=raw["payload"], metadata=raw.get("metadata", {}))

    def put(self, key: str, payload: dict, *, metadata: Optional[dict] = None) -> None:
        if not self.enabled:
            return
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "payload": payload,
            "metadata": {
                "written_at": datetime.now(timezone.utc).isoformat(),
                "prompt_version": self.prompt_version,
                "schema_version": self.schema_version,
                **(metadata or {}),
            },
        }
        # Atomic write: tmp file then rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{key}.", suffix=".json.tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        self.stats.writes += 1
