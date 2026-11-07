"""Candidate promo windows: every legal single-segment cut in an episode, scored by rules.

A window starts at a safe cut point whose first 3 seconds contain a hook beat and ends at a
safe cut point 20-60 s later. All such windows are scored (a few thousand per episode at
most), then the best non-overlapping ones are kept. Multi-segment candidates come in M4.
`build_window` turns a model's (hook block, cliffhanger block) proposal into a legal window.

Speed: the hot loop scores windows from per-block arrays and returns only a total; the
explanatory features are computed again, with the same code path, for the kept windows only.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field

from .lexicon import Lexicon
from .signals import BlockSignals
from .timeline import CutPoint

_EPS = 1e-6


@dataclass(frozen=True)
class Segment:
    episode: int
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 2)


@dataclass
class Candidate:
    candidate_id: str
    kind: str  # "single" now; "bridge" / "tightened" / "montage" in M4
    segments: list[Segment]
    hook_block: str
    cliff_block: str
    rule_scores: dict  # hook, cliffhanger, standalone, pacing, length, total
    features: dict = field(default_factory=dict)
    source: str = "rules"  # "rules" (window search) or "llm" (proposed by the model, built by code)

    @property
    def duration_s(self) -> float:
        return round(sum(s.duration_s for s in self.segments), 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_s"] = self.duration_s
        return d


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def overlap(a: Segment, b: Segment) -> float:
    """Shared time as a fraction of the shorter segment (0 if different episodes)."""
    if a.episode != b.episode:
        return 0.0
    shared = min(a.end_s, b.end_s) - max(a.start_s, b.start_s)
    return max(0.0, shared) / max(_EPS, min(a.duration_s, b.duration_s))


def length_fit(d: float, p: dict) -> float:
    if p["sweet_min_s"] <= d <= p["sweet_max_s"]:
        return 1.0
    if d < p["sweet_min_s"]:
        return 0.8 + 0.2 * (d - p["min_s"]) / (p["sweet_min_s"] - p["min_s"])
    return 1.0 - 0.2 * (d - p["sweet_max_s"]) / (p["max_s"] - p["sweet_max_s"])


class _Index:
    """One episode's blocks (sorted by start) as parallel arrays, for fast window scoring."""

    def __init__(self, ep: dict, sig: dict[str, BlockSignals], lex: Lexicon):
        blocks = self.blocks = ep["blocks"]
        self.duration = ep["duration_s"]
        self.starts = [b["start_s"] for b in blocks]
        self.ends = [b["end_s"] for b in blocks]
        self.sigs = [sig[b["block_id"]] for b in blocks]
        self.hook = [s.hook for s in self.sigs]
        self.cliff = [s.cliff for s in self.sigs]
        self.res = [s.resolution for s in self.sigs]
        self.inten = [s.intensity for s in self.sigs]
        self.debt = [s.debt for s in self.sigs]
        self.question = [s.question for s in self.sigs]
        self.is_line = [b["kind"] == "DIALOGUE" for b in blocks]
        self.is_camera = [b["kind"] == "CAMERA" for b in blocks]
        self.label = [b["speaker"]["label"] if b["speaker"] else None for b in blocks]
        self.vo = [bool(b["speaker"]) and b["speaker"]["voice"] == "V.O." for b in blocks]
        self.opener = [bool(b["speaker"]) and lex.starts_with("openers", b["text"]) for b in blocks]
        self.busy = [(b["start_s"], b["end_s"]) for b in blocks if b["kind"] in ("DIALOGUE", "ACTION")]
        self.busy_starts = [s for s, _ in self.busy]

    def lo(self, t: float) -> int:
        """Index of the first block starting at or after t."""
        return bisect_left(self.starts, t - _EPS)

    def upto(self, t: float) -> int:
        """Index just past the last block starting at or before t."""
        return bisect_right(self.starts, t + _EPS)

    def longest_quiet(self, t0: float, t1: float) -> float:
        """Longest stretch in [t0, t1] with no spoken line and no ACTION on screen."""
        if t1 <= t0:
            return 0.0
        spans = [(max(t0, s), min(t1, e)) for s, e in self.busy[: bisect_left(self.busy_starts, t1)] if e > t0]
        longest, cursor = 0.0, t0
        for s, e in sorted(spans):
            longest = max(longest, s - cursor)
            cursor = max(cursor, e)
        return max(longest, t1 - cursor)


def _hook_part(idx: _Index, cut: CutPoint, p: dict) -> tuple[float, int | None, float, float]:
    """(opening score, hook block index, onset, strength of a hook the cut skips just before it)."""
    i0, i1 = idx.lo(cut.t), idx.upto(cut.t + p["hook_max_onset_s"])
    if i0 >= i1:
        return 0.0, None, 0.0, 0.0
    hook_i = max(range(i0, i1), key=lambda i: (idx.hook[i], -idx.starts[i]))
    onset = round(idx.starts[hook_i] - cut.t, 2)
    target, limit = p["hook_target_onset_s"], p["hook_max_onset_s"]
    factor = 1.0 if onset <= target else 1.0 - 0.2 * (onset - target) / (limit - target)
    score = idx.hook[hook_i] * factor + (0.03 if cut.shot_boundary else -0.03)
    # Starting on the reaction after a slap cuts off the slap itself: penalise skipped strong beats.
    skipped = max((idx.hook[i] for i in range(idx.lo(cut.t - limit), i0)), default=0.0)
    if skipped >= 0.6:
        score -= 0.2 * skipped
    return _clamp(score), hook_i, onset, skipped


def _evaluate(idx: _Index, cut_in: CutPoint, cut_out: CutPoint, hook_i: int, hook_score: float,
              onset: float, skipped: float, cfg: dict, detail: bool = False, require_cliff: bool = True):
    """Score one window. Returns None if it has no usable cliffhanger (unless require_cliff is
    False); otherwise (rounded total, None) in the hot loop, or a full result dict when detail=True."""
    p, c, w = cfg["promo"], cfg["candidates"], cfg["score"]["weights"]
    t0, t1 = cut_in.t, cut_out.t
    i0, i1 = idx.lo(t0), idx.lo(t1)                      # blocks starting inside the window
    a0, a1 = i1, idx.lo(t1 + c["lookahead_s"])           # blocks right after the cut
    tail_from, near = t1 - c["tail_s"] - _EPS, t1 - c["tail_near_s"] - _EPS
    # the ending = every beat still on screen or audible in the last tail_s seconds
    tail = [i for i in range(i0, i1) if idx.ends[i] >= tail_from]
    if not tail:
        return None

    # --- cliffhanger: strongest beat near the cut, plus what the cut withholds
    best_i, best_key = tail[0], None
    for i in tail:
        key = ((1.0 if idx.ends[i] >= near else 0.75) * idx.cliff[i], idx.starts[i])
        if best_key is None or key > best_key:
            best_i, best_key = i, key
    cliff = best_key[0]
    q = None
    for i in tail:
        if idx.is_line[i] and idx.question[i] and (q is None or idx.starts[i] > idx.starts[q]):
            q = i
    unanswered, open_question = False, None
    if q is not None:
        q_end, q_label = idx.ends[q], idx.label[q]
        answered = any(idx.is_line[j] and idx.starts[j] >= q_end - _EPS and idx.label[j] != q_label
                       for j in range(i0, i1))
        if not answered:
            cliff += 0.15
            unanswered, open_question = True, idx.blocks[q]["text"]
            nxt = next((j for j in range(a0, a1) if idx.is_line[j]), None)
            if nxt is not None and idx.label[nxt] != q_label:
                cliff += 0.05
    payoff = max((idx.hook[j] for j in range(a0, a1)), default=0.0)
    cliff += 0.15 * payoff + 0.05 * max((idx.res[j] for j in range(a0, a1)), default=0.0)
    released = max(((1.0 if idx.ends[i] >= near else 0.75) * idx.res[i] for i in tail), default=0.0)
    cliff -= 0.3 * released
    at_episode_end = t1 >= idx.duration - _EPS
    if at_episode_end:
        cliff += 0.15
    if cut_out.inside_action:
        cliff -= 0.1
    cliff = _clamp(cliff)
    if require_cliff and cliff < c["min_cliff"]:
        return None

    # --- standalone (rules can only approximate this; the LLM judge does it properly)
    lines = [i for i in range(i0, i1) if idx.is_line[i]]
    speakers = {idx.label[i] for i in lines}
    debt = sum(idx.debt[i0:i1])
    standalone = 1.0 - 0.1 * max(0, len(speakers) - 3) - 0.15 * min(2.0, debt)
    mid_exchange = bool(lines) and idx.starts[lines[0]] - t0 <= 1.5 and idx.opener[lines[0]]
    if mid_exchange:
        standalone -= 0.2
    if any(idx.vo[i] for i in lines):
        standalone -= 0.05
    standalone = _clamp(standalone)

    # --- pacing
    d = t1 - t0
    speech = sum(min(t1, idx.ends[i]) - max(t0, idx.starts[i]) for i in lines) / d
    coverage = 1.0 if 0.35 <= speech <= 0.85 else _clamp(1 - min(abs(speech - 0.35), abs(speech - 0.85)) / 0.35)
    active = [idx.inten[i] for i in range(i0, i1) if not idx.is_camera[i]]
    quiet = idx.longest_quiet(t0, t1 - c["tail_s"])
    pacing = _clamp(0.5 * coverage + 0.5 * (sum(active) / len(active) if active else 0.0) - (0.15 if quiet > 6 else 0.0))

    length = length_fit(d, p)
    total = (w["hook"] * hook_score + w["cliffhanger"] * cliff + w["standalone"] * standalone
             + w["pacing"] * pacing + w["length"] * length)
    if not detail:
        return round(total, 3), None

    reasons = [idx.sigs[best_i].cliff_group or "beat"]
    if unanswered:
        reasons.append("unanswered question")
    if payoff >= 0.5:
        reasons.append("cuts before the payoff")
    if at_episode_end:
        reasons.append("episode ending")
    return {
        "hook_block": idx.blocks[hook_i]["block_id"],
        "cliff_block": idx.blocks[best_i]["block_id"],
        "scores": {k: round(v, 3) for k, v in (("hook", hook_score), ("cliffhanger", cliff), ("standalone", standalone),
                                                ("pacing", pacing), ("length", length), ("total", total))},
        "features": {
            "hook_onset_s": onset,
            "hook_group": idx.sigs[hook_i].hook_group,
            "skipped_hook": round(skipped, 2),
            "cliff_reasons": reasons,
            "speakers": sorted(speakers),
            "speech_ratio": round(speech, 2),
            "longest_quiet_s": round(quiet, 2),
            "clean_in": cut_in.shot_boundary,
            "clean_out": cut_out.shot_boundary,
            "cut_out_mid_action": cut_out.inside_action,
            "open_question": open_question,
            "episode_end": at_episode_end,
            "mid_exchange_start": mid_exchange,
            "released_at_end": round(released, 2),
            "graphic": any(idx.sigs[i].graphic for i in range(i0, i1)),
            "context_debt": round(debt, 2),
        },
    }


def episode_candidates(ep: dict, sig: dict[str, BlockSignals], cuts: list[CutPoint], lex: Lexicon,
                       cfg: dict) -> tuple[list[Candidate], int]:
    """Best non-overlapping single-segment windows of one episode (best first), and how many
    legal windows were scored."""
    p, c = cfg["promo"], cfg["candidates"]
    idx, n = _Index(ep, sig, lex), ep["episode_number"]
    times = [cut.t for cut in cuts]
    scored = []
    for cut_in in cuts:
        hook_score, hook_i, onset, skipped = _hook_part(idx, cut_in, p)
        if hook_i is None or idx.hook[hook_i] < c["min_hook"]:
            continue
        lo = bisect_left(times, cut_in.t + p["min_s"] - _EPS)
        hi = bisect_right(times, cut_in.t + p["max_s"] + _EPS)
        for cut_out in cuts[lo:hi]:
            result = _evaluate(idx, cut_in, cut_out, hook_i, hook_score, onset, skipped, cfg)
            if result is not None:
                scored.append((result[0], cut_in, cut_out, hook_i, hook_score, onset, skipped))

    scored.sort(key=lambda x: (-x[0], x[1].t, x[2].t))
    kept = []
    for entry in scored:
        seg = Segment(n, entry[1].t, entry[2].t)
        if all(overlap(seg, other) <= c["max_overlap"] for other, _ in kept):
            kept.append((seg, entry))
        if len(kept) >= c["keep_per_episode"]:
            break

    # Every strong opening gets its own best window judged, even when a higher-scoring window
    # overlaps it: two promos that open on different lines are different promos. (M3: a strong
    # opening line was dropped for a window starting 8 s later.)
    def shown_at_start(i: int) -> bool:
        return any(s.start_s - _EPS <= idx.starts[i] <= s.start_s + p["hook_max_onset_s"] + _EPS for s, _ in kept)

    extra = 0
    for i in sorted(range(len(idx.blocks)), key=lambda i: (-idx.hook[i], idx.starts[i])):
        if extra >= c["max_extra_anchors"] or idx.hook[i] < c["anchor_min_hook"]:
            break
        if shown_at_start(i):
            continue
        best = next((e for e in scored if e[3] == i), None)
        if best is not None:
            kept.append((Segment(n, best[1].t, best[2].t), best))
            extra += 1

    candidates = []
    n_best = len(kept) - extra
    for i, (seg, (_, cut_in, cut_out, hook_i, hook_score, onset, skipped)) in enumerate(kept, start=1):
        r = _evaluate(idx, cut_in, cut_out, hook_i, hook_score, onset, skipped, cfg, detail=True)
        r["features"]["extra_anchor"] = i > n_best  # kept for its distinct opening, not its score
        candidates.append(Candidate(
            candidate_id=f"E{n:03d}-C{i:02d}",
            kind="single",
            segments=[seg],
            hook_block=r["hook_block"],
            cliff_block=r["cliff_block"],
            rule_scores=r["scores"],
            features=r["features"],
        ))
    return candidates, len(scored)


def build_window(ep: dict, sig: dict[str, BlockSignals], cuts: list[CutPoint], lex: Lexicon, cfg: dict,
                 hook_id: str, cliff_id: str, candidate_id: str) -> tuple[Candidate | None, str]:
    """Turn a model's pick (the beat to open on, the beat to end on) into a legal window.

    Code chooses the cut points: the cut in is a clean shot change at most 1.5 s before the hook
    (else the last safe cut within 3 s before it); the cut out is the first safe cut after the
    cliffhanger beat ends, stretched to 20 s if needed. Returns (candidate, "") or (None, reason).
    """
    p, c = cfg["promo"], cfg["candidates"]
    by_id = {b["block_id"]: b for b in ep["blocks"]}
    hook, cliff = by_id.get(hook_id), by_id.get(cliff_id)
    if hook is None or cliff is None:
        return None, "unknown block"
    if cliff["start_s"] <= hook["start_s"]:
        return None, "cliffhanger does not come after the hook"

    h = hook["start_s"]
    near = [k for k in cuts if h - p["hook_target_onset_s"] - _EPS <= k.t <= h + _EPS]
    clean = [k for k in near if k.shot_boundary]
    if clean:
        cut_in = clean[0]
    else:
        ins = [k for k in cuts if h - p["hook_max_onset_s"] - _EPS <= k.t <= h + _EPS]
        if not ins:
            return None, "no safe cut within 3 s before the hook"
        cut_in = ins[-1]

    need = max(cliff["end_s"], cut_in.t + p["min_s"])
    cut_out = next((k for k in cuts if k.t >= need - _EPS), None)
    if cut_out is None:
        return None, "the episode ends before a 20 s promo"
    if cut_out.t - cliff["end_s"] > c["tail_s"] + _EPS:
        return None, (f"hook and cliffhanger are too close: a 20 s promo would run "
                      f"{cut_out.t - cliff['end_s']:.1f} s past the cliffhanger")
    if cut_out.t - cut_in.t > p["max_s"] + _EPS:
        return None, f"too long: {cut_out.t - cut_in.t:.1f} s from hook to cliffhanger"

    idx = _Index(ep, sig, lex)
    hook_score, hook_i, onset, skipped = _hook_part(idx, cut_in, p)
    r = None if hook_i is None else _evaluate(idx, cut_in, cut_out, hook_i, hook_score, onset, skipped, cfg,
                                              detail=True, require_cliff=False)
    if r is None:
        return None, "nothing on screen to judge"
    return Candidate(
        candidate_id=candidate_id,
        kind="single",
        segments=[Segment(ep["episode_number"], cut_in.t, cut_out.t)],
        hook_block=hook_id,
        cliff_block=cliff_id,
        rule_scores=r["scores"],
        features=r["features"],
        source="llm",
    ), ""
