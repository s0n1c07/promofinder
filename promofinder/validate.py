"""Hard rules every promo must pass, whichever judge proposed it.

A promo that breaks any rule is dropped and counted; nothing is silently repaired here.
"""

from __future__ import annotations

from .timeline import inside_line
from .windows import Segment

RULES = {
    "segment_count": "has 1 to 3 segments",
    "segment_bounds": "every segment lies inside its episode and lasts at least 3 s",
    "safe_cuts": "no segment starts or ends inside a spoken line",
    "story_order": "segments are in story order and do not overlap",
    "duration": "total length is 20-60 s",
    "ids_exist": "the hook and cliffhanger block ids exist",
    "hook_onset": "the hook beat starts within 3 s of the promo start",
    "cliff_inside": "the cliffhanger beat starts inside the last segment",
}


def block_index(episodes: dict[int, dict]) -> dict[str, dict]:
    return {b["block_id"]: b for ep in episodes.values() for b in ep["blocks"]}


def check_promo(segments: list[Segment], hook_block: str, cliff_block: str, episodes: dict[int, dict],
                index: dict[str, dict], cfg: dict) -> list[str]:
    """Names of the rules this promo breaks (empty if it is valid)."""
    p, eps = cfg["promo"], cfg["timeline"]["eps_s"]
    broken: set[str] = set()

    if not 1 <= len(segments) <= p["max_segments"]:
        broken.add("segment_count")
    for s in segments:
        ep = episodes.get(s.episode)
        if (ep is None or s.start_s < -eps or s.end_s > ep["duration_s"] + eps
                or s.duration_s < p["min_segment_s"] - eps):
            broken.add("segment_bounds")
            continue
        if inside_line(ep, s.start_s, eps) or inside_line(ep, s.end_s, eps):
            broken.add("safe_cuts")
    spans = [(s.episode, s.start_s, s.end_s) for s in segments]
    if spans != sorted(spans) or any(a.episode == b.episode and b.start_s < a.end_s - eps
                                     for a, b in zip(segments, segments[1:])):
        broken.add("story_order")
    total = sum(s.duration_s for s in segments)
    if not p["min_s"] - eps <= total <= p["max_s"] + eps:
        broken.add("duration")

    hook, cliff = index.get(hook_block), index.get(cliff_block)
    if hook is None or cliff is None:
        broken.add("ids_exist")
    elif segments:
        first, last = segments[0], segments[-1]
        if (int(hook_block[1:4]) != first.episode
                or not first.start_s - eps <= hook["start_s"] <= first.start_s + p["hook_max_onset_s"] + eps):
            broken.add("hook_onset")
        if int(cliff_block[1:4]) != last.episode or not last.start_s - eps <= cliff["start_s"] < last.end_s:
            broken.add("cliff_inside")
    return sorted(broken)
