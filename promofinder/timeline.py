"""Where a promo may start or end.

A safe cut point is a block boundary (or the episode's start or end) that does not fall
inside a spoken line. About 1 in 6 dialogue lines runs across a camera cut in this pack, so
shot boundaries alone are not enough.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CutPoint:
    t: float
    shot_boundary: bool  # a camera shot starts or ends exactly here (clean cut)
    inside_action: bool  # an ACTION block is in progress across this time (cuts mid-movement)


def _strictly_inside(t: float, spans: list[tuple[float, float]], eps: float) -> bool:
    return any(s + eps < t < e - eps for s, e in spans)


def line_spans(ep: dict) -> list[tuple[float, float]]:
    return [(b["start_s"], b["end_s"]) for b in ep["blocks"] if b["kind"] == "DIALOGUE"]


def inside_line(ep: dict, t: float, eps: float = 0.05) -> bool:
    return _strictly_inside(t, line_spans(ep), eps)


def reply_opening(ep: dict, t: float, gap_s: float = 3.0) -> dict | None:
    """The question a cut-in at t would leave out: the last spoken line before t ends in "?", another
    speaker has the first line after t, and both sit within gap_s of t. A part opening at t would
    start on an answer to a question the viewer never hears. None if t opens cleanly."""
    lines = [b for b in ep["blocks"] if b["kind"] == "DIALOGUE"]
    first = next((b for b in lines if b["start_s"] >= t - 1e-6), None)
    before = [b for b in lines if b["end_s"] <= t + 1e-6]
    if first is None or not before:
        return None
    q = max(before, key=lambda b: (b["end_s"], b["start_s"]))
    if (q["text"].rstrip().endswith("?") and q["speaker"]["label"] != first["speaker"]["label"]
            and t - q["end_s"] <= gap_s and first["start_s"] - t <= gap_s):
        return q
    return None


def cut_points(ep: dict, eps: float = 0.05) -> list[CutPoint]:
    """All safe cut points of an episode, in time order."""
    lines = line_spans(ep)
    actions = [(b["start_s"], b["end_s"]) for b in ep["blocks"] if b["kind"] == "ACTION"]
    shots = {b["start_s"] for b in ep["blocks"] if b["kind"] == "CAMERA"}
    shots |= {b["end_s"] for b in ep["blocks"] if b["kind"] == "CAMERA"}
    times = {0.0, ep["duration_s"]}
    times |= {b["start_s"] for b in ep["blocks"]} | {b["end_s"] for b in ep["blocks"]}
    return [
        CutPoint(t, t in shots, _strictly_inside(t, actions, eps))
        for t in sorted(times)
        if 0.0 <= t <= ep["duration_s"] and not _strictly_inside(t, lines, eps)
    ]
