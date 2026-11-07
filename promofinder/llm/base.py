"""One small interface in front of every model provider.

A client takes a system prompt, a user prompt and a JSON Schema and returns the parsed JSON
reply with its token usage. The judge never knows which provider answered, so adding a
provider means writing one class with `fingerprint()` and `complete_json()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(RuntimeError):
    """The model could not be reached, or its reply was not usable JSON."""


class LLMUnavailable(LLMError):
    """The model server is down or refuses connections. A run stops on this instead of quietly
    falling back to the rules for every remaining episode; the cache lets a re-run resume."""


@dataclass
class LLMResult:
    data: dict                   # the parsed JSON reply
    text: str                    # the raw reply, kept untouched for llm_raw/
    input_tokens: int = 0        # prompt tokens the provider reported
    output_tokens: int = 0
    seconds: float = 0.0         # wall time of this call (about 0 when served from the cache)
    compute_seconds: float = 0.0 # time the model took when the answer was first computed
    cached: bool = False
    finish: str = "stop"         # why generation stopped, as the provider reports it
    model: str = ""
    timing: dict = field(default_factory=dict)  # provider's breakdown, e.g. load / read prompt / generate seconds


class LLMClient(Protocol):
    name: str  # e.g. "ollama:qwen3.5:4b"

    def fingerprint(self) -> dict:
        """Everything that changes the model's answers: provider, model, version or digest, options."""
        ...

    def complete_json(self, system: str, user: str, schema: dict) -> LLMResult: ...
