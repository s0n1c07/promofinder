"""Local models through Ollama's HTTP API: no key, no per-token cost, standard library only.

The JSON Schema goes in the request's `format` field, so Ollama constrains decoding to it.
The reply always parses, and every block ID in it is one of the values the schema allows.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .base import LLMError, LLMResult, LLMUnavailable


class OllamaClient:
    def __init__(self, model: str, options: dict, timeout_s: float = 900.0, max_output_tokens: int = 2048):
        self.model = model
        self.name = f"ollama:{model}"
        host = os.environ.get("OLLAMA_HOST") or options.get("host") or "http://localhost:11434"
        self.host = (host if host.startswith("http") else f"http://{host}").rstrip("/")
        self.timeout_s = timeout_s
        self.keep_alive = options.get("keep_alive", "30m")
        self.options = {
            "temperature": options.get("temperature", 0.0),
            "seed": options.get("seed", 7),
            "num_ctx": options.get("num_ctx", 8192),
            "num_predict": max_output_tokens,
        }
        self._info: dict | None = None

    # ------------------------------------------------------------------ HTTP
    def _request(self, path: str, body: dict | None, timeout: float) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.host + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"Ollama {path} returned HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise LLMError(f"Ollama at {self.host} did not answer within {timeout:.0f} s") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise LLMError(f"Ollama at {self.host} did not answer within {timeout:.0f} s") from exc
            raise LLMUnavailable(f"cannot reach Ollama at {self.host} ({exc}). Start it with `ollama serve`, "
                                 f"or use --judge offline") from exc

    # ------------------------------------------------------------------ model info
    def info(self) -> dict:
        """Capabilities and digest of the model. Fails early, with the fix, if the model is missing."""
        if self._info is None:
            try:
                show = self._request("/api/show", {"model": self.model}, timeout=60)
            except LLMError as exc:
                if "HTTP 404" in str(exc):
                    raise LLMError(f"model {self.model!r} is not downloaded: run `ollama pull {self.model}`") from exc
                raise
            tags = self._request("/api/tags", None, timeout=60).get("models", [])
            names = {self.model, f"{self.model}:latest"}
            digest = next((m.get("digest", "") for m in tags if m.get("name") in names or m.get("model") in names), "")
            self._info = {
                "capabilities": show.get("capabilities", []),
                "digest": digest,
                "details": show.get("details", {}),
            }
        return self._info

    def _thinks(self) -> bool:
        return "thinking" in self.info()["capabilities"]

    def fingerprint(self) -> dict:
        info = self.info()
        return {
            "provider": "ollama",
            "model": self.model,
            "digest": info["digest"],
            "quantization": info["details"].get("quantization_level"),
            "options": self.options,
            "think": False if self._thinks() else None,
        }

    # ------------------------------------------------------------------ call
    def _options_for(self, system: str, user: str) -> dict:
        """Ollama silently drops the start of a prompt that overflows the context window, so a
        prompt that might not fit (about 3 characters per token, plus the reply) gets a bigger one."""
        need = (len(system) + len(user)) // 3 + self.options["num_predict"]
        if need <= self.options["num_ctx"]:
            return self.options
        size = self.options["num_ctx"]
        while size < need:
            size *= 2
        return self.options | {"num_ctx": size}

    def complete_json(self, system: str, user: str, schema: dict) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self._options_for(system, user),
        }
        if self._thinks():
            body["think"] = False  # a judge needs a verdict, not a long reasoning trace on a CPU
        started = time.perf_counter()
        resp = self._request("/api/chat", body, timeout=self.timeout_s)
        seconds = round(time.perf_counter() - started, 2)
        text = (resp.get("message") or {}).get("content") or ""
        finish = resp.get("done_reason") or "stop"
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"reply is not JSON (stopped because: {finish}): {text[:200]!r}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"reply is JSON but not an object: {text[:200]!r}")
        return LLMResult(
            data=data,
            text=text,
            input_tokens=int(resp.get("prompt_eval_count") or 0),
            output_tokens=int(resp.get("eval_count") or 0),
            seconds=seconds,
            compute_seconds=seconds,
            finish=finish,
            model=resp.get("model") or self.model,
            timing={name: round(resp.get(key, 0) / 1e9, 2) for name, key in
                    (("load_s", "load_duration"), ("prompt_s", "prompt_eval_duration"),
                     ("generate_s", "eval_duration"))},
        )
