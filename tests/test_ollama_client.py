"""The Ollama adapter against a fake Ollama server (no model download needed)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from promofinder.llm.base import LLMError
from promofinder.llm.ollama import OllamaClient


class _FakeOllama(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "thinker:4b", "digest": "abc123"},
                                        {"name": "plain:4b", "digest": "def456"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, body))
        if self.path == "/api/show":
            if body["model"] not in ("thinker:4b", "plain:4b"):
                return self._send(404, {"error": f"model '{body['model']}' not found"})
            caps = ["completion", "thinking"] if body["model"] == "thinker:4b" else ["completion"]
            return self._send(200, {"capabilities": caps, "details": {"quantization_level": "Q4_K_M"}})
        if self.path == "/api/chat":
            return self._send(200, {"model": body["model"], "done": True, "done_reason": "stop",
                                    "message": {"role": "assistant", "content": '{"ok": true}'},
                                    "prompt_eval_count": 123, "eval_count": 45})
        self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    httpd.requests = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def _client(server, model: str) -> OllamaClient:
    return OllamaClient(model, {"host": f"http://127.0.0.1:{server.server_address[1]}"})


def test_sends_the_schema_and_turns_thinking_off(server):
    client = _client(server, "thinker:4b")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    result = client.complete_json("system text", "user text", schema)
    path, body = server.requests[-1]
    assert path == "/api/chat" and body["format"] == schema and body["stream"] is False
    assert body["think"] is False
    assert body["options"]["temperature"] == 0.0 and body["options"]["seed"] == 7
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert result.data == {"ok": True} and (result.input_tokens, result.output_tokens) == (123, 45)
    assert client.fingerprint()["digest"] == "abc123"


def test_models_without_thinking_get_no_think_field(server):
    _client(server, "plain:4b").complete_json("s", "u", {"type": "object"})
    assert "think" not in server.requests[-1][1]


def test_a_missing_model_says_how_to_fix_it(server):
    with pytest.raises(LLMError, match="ollama pull missing:1b"):
        _client(server, "missing:1b").fingerprint()


def test_no_server_says_how_to_fix_it(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    with pytest.raises(LLMError, match="cannot reach Ollama"):
        OllamaClient("any:1b", {"host": "http://127.0.0.1:9"}).fingerprint()


def test_a_prompt_that_would_overflow_gets_a_bigger_window(server):
    client = _client(server, "plain:4b")
    client.complete_json("s", "short", {"type": "object"})
    assert server.requests[-1][1]["options"]["num_ctx"] == 8192
    client.complete_json("s", "x" * 30000, {"type": "object"})   # ~10K tokens + the reply
    assert server.requests[-1][1]["options"]["num_ctx"] == 16384
