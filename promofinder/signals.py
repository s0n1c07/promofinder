"""Rule signals per block: how hook-like, cliffhanger-like and resolving each beat is.

Every score is in [0, 1] and comes with the words that produced it, so a person can see
why the rules liked a moment. These signals find candidates and score the rules-only
baseline; the LLM judge (M3) adds the meaning that word lists cannot see.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .lexicon import Lexicon

_WORD = re.compile(r"[A-Za-z']+")
_TRAILING = ("...", "…", "—", "--")


@dataclass
class BlockSignals:
    block_id: str
    hook: float
    cliff: float
    resolution: float
    intensity: float
    debt: float
    graphic: bool
    question: bool
    hook_group: str | None
    cliff_group: str | None
    terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 3)


def delivery_levels(tags: list[str], lex: Lexicon) -> tuple[float, float]:
    """(intensity, softness) from delivery tags such as ['Cold', 'Commanding']."""
    intensity = soft = 0.0
    for tag in tags:
        if lex.find("delivery.high", tag):
            intensity = max(intensity, 1.0)
        elif lex.find("delivery.medium", tag):
            intensity = max(intensity, 0.6)
        elif lex.find("delivery.soft", tag):
            soft = 1.0
    return intensity, soft


def block_signals(block: dict, lex: Lexicon) -> BlockSignals:
    text, kind = block["text"], block["kind"]
    hook_lex, hook_group, hook_terms, hook_hits = lex.strongest("hook", text, kind)
    hook_lex = min(1.0, hook_lex + 0.1 * max(0, hook_hits - 1))
    emphasis = bool(lex.find("camera.emphasis", text)) if kind == "CAMERA" else False
    sfx_hit = bool(lex.find("sfx.hit", text)) if kind == "SOUND_FX" else False

    soft = 0.0
    if kind == "DIALOGUE":
        intensity, soft = delivery_levels(block["speaker"]["delivery"], lex)
        if "!" in text:
            intensity = 1.0 if intensity >= 0.6 else 0.6
    elif kind == "SOUND_FX":
        intensity = 0.7 if sfx_hit else 0.3
    elif kind == "ACTION":
        intensity = 0.6 if hook_group == "physical" else 0.25
    else:
        intensity = 0.5 if emphasis else 0.1

    question = kind == "DIALOGUE" and "?" in text
    words = len(_WORD.findall(text))
    punch = 0.15 if kind == "DIALOGUE" and words <= 6 and intensity >= 0.6 else 0.0
    shocked_question = 0.1 if question and intensity >= 0.6 else 0.0
    hook = 0.55 * hook_lex + 0.35 * intensity + punch + shocked_question + (0.1 if emphasis or sfx_hit else 0.0)

    cliff_lex, cliff_group, cliff_terms, _ = lex.strongest("cliff", text, kind)
    tension = 0.3 if kind == "SOUND_FX" and lex.find("sfx.tension", text) else 0.0
    trailing = 0.1 if text.rstrip().endswith(_TRAILING) else 0.0
    cliff = 0.6 * cliff_lex + 0.25 * intensity + (0.25 if question else 0.0) + tension + trailing
    if question and cliff_group is None:
        cliff_group = "question"

    resolution_terms = lex.find("resolution", text, kind)
    resolution = 0.7 * (lex.weights["resolution"] if resolution_terms else 0.0) + 0.3 * soft
    debt_terms = lex.find("context_debt", text, kind)

    return BlockSignals(
        block_id=block["block_id"],
        hook=_clamp(hook),
        cliff=_clamp(cliff),
        resolution=_clamp(resolution),
        intensity=_clamp(intensity),
        debt=_clamp(0.5 * len(debt_terms)),
        graphic=bool(lex.find("graphic", text, kind)),
        question=question,
        hook_group=hook_group,
        cliff_group=cliff_group,
        terms=sorted(set(hook_terms + cliff_terms + resolution_terms + debt_terms)),
    )


def episode_signals(ep: dict, lex: Lexicon) -> dict[str, BlockSignals]:
    return {b["block_id"]: block_signals(b, lex) for b in ep["blocks"]}
