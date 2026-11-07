"""A scripted stand-in for a model: answers any judge prompt with a schema-valid reply."""

import json
import re

from promofinder.llm.base import LLMError, LLMResult

_BLOCK = re.compile(r"^(B\d{3}) ([\d.]+)-([\d.]+) ", re.M)


def default_reply(user: str, schema: dict) -> dict:
    """Every clip: first hook option scored 5, last ending option scored 4, a real quote.
    An episode with no clips gets one proposal: open about 28 s before the end, end on the last beat.
    A montage call gets one montage: the first beat on the menu joined to the last one."""
    if "montages" in schema["properties"]:
        ids = schema["properties"]["montages"]["items"]["properties"]["beats"]["items"]["enum"]
        if ids[0][:4] == ids[-1][:4]:
            return {"montages": []}
        return {"montages": [{"beats": [ids[0], ids[-1]], "hook": 4, "cliff": 5, "standalone": 3,
                              "trope": "betrayal", "flags": [], "why": "Joins an early slight to a late reveal.",
                              "question": "What happens next?"}]}
    clips = {}
    for cid, sub in schema["properties"]["clips"]["properties"].items():
        props = sub["properties"]
        clips[cid] = {
            "hook_block": props["hook_block"]["enum"][0], "hook": 5,
            "cliff_block": props["cliff_block"]["enum"][-1], "question": "Who told her?",
            "cliff": 4, "standalone": 3, "trope": "cruelty", "flags": ["spoiler"],
            "why": "Opens on a slap and leaves a threat hanging.",
        }
    missed = []
    if not clips:
        blocks = [(b, float(s), float(e)) for b, s, e in _BLOCK.findall(user)]
        end = max(e for _, _, e in blocks)
        hook = next(b for b, s, _ in blocks if s >= end - 28)
        missed.append({"hook_block": hook, "hook": 4, "cliff_block": blocks[-1][0], "question": "What now?",
                       "cliff": 5, "standalone": 4, "trope": "reveal", "flags": [],
                       "why": "A late reveal the rules missed."})
    return {"clips": clips, "missed": missed}


class FakeClient:
    name = "fake:judge"

    def __init__(self, reply=default_reply, fail: str | None = None):
        self.reply, self.fail, self.calls = reply, fail, []

    def fingerprint(self) -> dict:
        return {"provider": "fake", "model": "judge"}

    def complete_json(self, system: str, user: str, schema: dict) -> LLMResult:
        self.calls.append((system, user, schema))
        if self.fail:
            raise LLMError(self.fail)
        data = self.reply(user, schema)
        text = json.dumps(data)
        return LLMResult(data=data, text=text, input_tokens=len(user) // 4, output_tokens=len(text) // 4,
                         seconds=0.01, compute_seconds=0.01)
