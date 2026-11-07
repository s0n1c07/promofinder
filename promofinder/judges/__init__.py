"""Judges score candidate windows. Every judge returns the same Judgement shape, so the
validator, ranking and export never care which one ran.

- `offline`: rules only, no model, no key (the baseline and the fallback).
- `ollama:<model>`: a local LLM through Ollama, grounded in block IDs (see llm.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

TROPES = ("betrayal", "reveal", "rescue", "cruelty", "danger", "romance", "revenge", "scheme")
FLAGS = (
    "graphic",             # may need a platform-policy check
    "spoiler",             # gives away a major twist (LLM judges only)
    "slow_start",          # hook lands after 1.5 s
    "late_start",          # opens just after a strong beat that the cut leaves out
    "resolves_at_end",     # tension is released before the cut
    "mid_exchange_start",  # opens on a reply, so the setup is missing
    "many_speakers",       # five or more speakers for a stranger to track
    "context_needed",      # leans on objects or events set up elsewhere
    "mid_action_cut",      # the cut lands while an action is still in progress
    "confusing",           # a stranger would not follow it (LLM judges only)
    "judge_disagrees",     # model and rules differ sharply on hook or cliffhanger: a person should look
)


@dataclass
class Judgement:
    candidate_id: str
    judge: str
    hook_block: str
    hook_score: float
    cliff_block: str
    cliff_score: float
    standalone_score: float
    why_it_works: str
    trope: str
    flags: list[str] = field(default_factory=list)
    open_question: str | None = None
    missing_context: str | None = None
    trim_start_block: str | None = None
    trim_end_block: str | None = None
    detail: dict = field(default_factory=dict)  # LLM judges: raw model scores, rule scores, grounding checks

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EpisodeVerdict:
    """What a judge returns for one episode."""

    judgements: list[Judgement]
    proposals: list[dict] = field(default_factory=list)  # windows or montages the model adds, by block ID
    calls: list[dict] = field(default_factory=list)      # one usage row per model call
    raw: dict | None = None                              # prompt and untouched reply, written to llm_raw/
    errors: list[str] = field(default_factory=list)


class Judge(Protocol):
    """Every judge scores an episode's candidates; `prev` is the previous episode when a bridge
    candidate starts in it. LLM judges also have propose_montages() and judge_montage()."""

    name: str

    def judge_episode(self, episode: dict, candidates: list, signals: dict,
                      prev: dict | None = None) -> EpisodeVerdict: ...

    def judge_proposal(self, proposal: dict, candidate, episode: dict, signals: dict) -> Judgement: ...


def get_judge(spec: str, cfg: dict) -> Judge:
    """'offline' (rules only) or '<provider>:<model>', e.g. 'ollama:qwen3.5:4b'."""
    if spec in ("offline", "rules"):
        from .offline import OfflineJudge

        return OfflineJudge(cfg["promo"]["hook_target_onset_s"])
    from ..llm import PROVIDERS, get_client

    if spec.partition(":")[0] in PROVIDERS:
        from .llm import LLMJudge

        return LLMJudge(get_client(spec, cfg), cfg)
    raise ValueError(f"unknown judge {spec!r}: use 'offline' or 'ollama:<model>' (e.g. ollama:qwen3.5:4b)")
