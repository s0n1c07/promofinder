"""Model access. `get_client("ollama:qwen3.5:4b", cfg)` returns a cached client for that model.

Providers: `ollama:<model>` (local, no key). Adding one (Anthropic, OpenAI, Gemini, a
llama.cpp server) means one class with `fingerprint()` and `complete_json()`; see base.py.
"""

from __future__ import annotations

from ..config import resolve
from .base import LLMClient, LLMError, LLMResult, LLMUnavailable
from .cache import CachedClient

PROVIDERS = ("ollama",)

__all__ = ["PROVIDERS", "CachedClient", "LLMClient", "LLMError", "LLMResult", "LLMUnavailable", "get_client"]


def get_client(spec: str, cfg: dict) -> LLMClient:
    provider, _, model = spec.partition(":")
    llm = cfg["llm"]
    if provider == "ollama":
        if not model:
            raise ValueError("name the model, e.g. --judge ollama:qwen3.5:4b")
        from .ollama import OllamaClient

        client: LLMClient = OllamaClient(model, llm["ollama"], timeout_s=llm["timeout_s"],
                                         max_output_tokens=llm["max_output_tokens"])
    else:
        raise ValueError(f"no adapter for provider {provider!r}; available: {', '.join(PROVIDERS)}")
    return CachedClient(client, resolve(llm["cache_dir"])) if llm["cache"] else client
