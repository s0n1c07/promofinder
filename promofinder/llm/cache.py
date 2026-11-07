"""Disk cache in front of any client.

The key is a hash of the client's fingerprint (provider, model, digest, options), the prompts
and the schema. The same question to the same model is answered once, so re-runs are free,
fast and identical, including for providers that offer no temperature control.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .base import LLMClient, LLMResult


class CachedClient:
    def __init__(self, inner: LLMClient, folder: Path):
        self.inner = inner
        self.name = inner.name
        self.folder = Path(folder)
        self.hits = 0
        self.misses = 0

    def fingerprint(self) -> dict:
        return self.inner.fingerprint()

    def key(self, system: str, user: str, schema: dict) -> str:
        blob = json.dumps({"client": self.inner.fingerprint(), "system": system, "user": user, "schema": schema},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def complete_json(self, system: str, user: str, schema: dict) -> LLMResult:
        key = self.key(system, user, schema)
        path = self.folder / key[:2] / f"{key}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            self.hits += 1
            return LLMResult(data=json.loads(rec["text"]), text=rec["text"], input_tokens=rec["input_tokens"],
                             output_tokens=rec["output_tokens"], seconds=0.0,
                             compute_seconds=rec["compute_seconds"], cached=True, finish=rec["finish"],
                             model=rec["model"], timing=rec.get("timing", {}))
        result = self.inner.complete_json(system, user, schema)
        self.misses += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "client": self.inner.fingerprint(), "text": result.text, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens, "compute_seconds": result.compute_seconds,
            "finish": result.finish, "model": result.model, "timing": result.timing,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return result
