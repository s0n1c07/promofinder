"""Promos that join 2-3 moments in story order.

- tightened: two moments of one episode with the middle cut out (proposed by the rules)
- bridge: the end of episode N straight into the start of N+1, where N+1 picks up directly (rules)
- montage: 2-3 moments from different episodes (the LLM picks the beats; code builds and checks)

Code chooses every cut. Each part is built around its beat at safe cut points: the first part
opens within 3 s of the hook (a clean shot change if one is 1.5 s away), the last part ends at
the first safe cut after the cliffhanger beat, and parts grow at their inner edges until the
promo reaches 20 s. The hard checks then apply exactly as for single windows.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right

from .lexicon import Lexicon
from .signals import BlockSignals
from .timeline import CutPoint, reply_opening
from .windows import _EPS, Candidate, Segment, _evaluate, _hook_part, _Index, length_fit, overlap

Part = list  # [episode number, cut in (CutPoint), cut out (CutPoint)]


def _ep_of(block_id: str) -> int:
    return int(block_id[1:4])


def _last_at_or_before(cuts: list[CutPoint], t: float) -> CutPoint | None:
    i = bisect_right([c.t for c in cuts], t + _EPS)
    return cuts[i - 1] if i else None


def _first_at_or_after(cuts: list[CutPoint], t: float) -> CutPoint | None:
    i = bisect_left([c.t for c in cuts], t - _EPS)
    return cuts[i] if i < len(cuts) else None


def _strictly_before(cuts: list[CutPoint], t: float) -> CutPoint | None:
    i = bisect_left([c.t for c in cuts], t - _EPS)
    return cuts[i - 1] if i else None


def _strictly_after(cuts: list[CutPoint], t: float) -> CutPoint | None:
    i = bisect_right([c.t for c in cuts], t + _EPS)
    return cuts[i] if i < len(cuts) else None


def _open_before(cuts: list[CutPoint], hook_start: float, p: dict) -> CutPoint | None:
    """Where to open so the hook lands fast: a clean shot change at most 1.5 s before it, else the
    last safe cut at most 3 s before it."""
    near = [c for c in cuts if hook_start - p["hook_target_onset_s"] - _EPS <= c.t <= hook_start + _EPS]
    clean = [c for c in near if c.shot_boundary]
    if clean:
        return clean[0]
    wide = [c for c in cuts if hook_start - p["hook_max_onset_s"] - _EPS <= c.t <= hook_start + _EPS]
    return wide[-1] if wide else None


def start_at_questions(parts: list[Part], eps: dict[int, dict], cuts: dict[int, list[CutPoint]], m: dict) -> str:
    """Move each later part's start back over a question the join would cut off, so it doesn't open
    on the answer (M5: 5 of 58 joined promos did). '' when done, else why the join can't work."""
    for i in range(1, len(parts)):
        n = parts[i][0]
        while (q := reply_opening(eps[n], parts[i][1].t, m["reply_gap_s"])) is not None:
            cin = _last_at_or_before(cuts[n], q["start_s"])
            floor = parts[i - 1][2].t + m["min_gap_s"] if parts[i - 1][0] == n else 0.0
            if cin is None or cin.t >= parts[i][1].t - _EPS or cin.t < floor - _EPS:
                return "a later part would open on an answer to a question the join cuts off"
            parts[i][1] = cin
    return ""


def build_parts(eps: dict[int, dict], cuts: dict[int, list[CutPoint]], beat_ids: list[str], cfg: dict,
                bridge: bool = False) -> tuple[list[Part] | None, str]:
    """Parts around the beats (first = hook, last = cliffhanger), or (None, why not)."""
    p, m, c = cfg["promo"], cfg["multiseg"], cfg["candidates"]
    if not 2 <= len(beat_ids) <= p["max_segments"]:
        return None, f"needs 2 to {p['max_segments']} beats"
    beats = []
    for bid in beat_ids:
        ep = eps.get(_ep_of(bid))
        block = next((b for b in ep["blocks"] if b["block_id"] == bid), None) if ep else None
        if block is None:
            return None, f"unknown block {bid}"
        beats.append(block)
    keys = [(_ep_of(b["block_id"]), b["start_s"]) for b in beats]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        return None, "beats are not in story order"

    parts: list[Part] = []
    last = len(beats) - 1
    for k, b in enumerate(beats):
        n, cl = _ep_of(b["block_id"]), cuts[_ep_of(b["block_id"])]
        if k == last:
            # The ending part stops at the first safe cut after the cliffhanger beat; its extra
            # seconds come before the beat (setup), never after it.
            cout = _first_at_or_after(cl, b["end_s"])
            cin = _last_at_or_before(cl, max(0.0, min(b["start_s"] - m["setup_s"], cout.t - m["min_part_s"])))
        else:
            cin = _open_before(cl, b["start_s"], p) if k == 0 else _last_at_or_before(cl, b["start_s"])
            if cin is None:
                return None, "no safe cut within 3 s before the hook"
            cout = _first_at_or_after(cl, max(b["end_s"], cin.t + m["min_part_s"])) or cl[-1]
        if cout.t - cin.t < p["min_segment_s"] - _EPS:
            return None, "a part would be shorter than 3 s"
        parts.append([n, cin, cout])
    if bridge:
        parts[0][2], parts[-1][1] = cuts[parts[0][0]][-1], cuts[parts[-1][0]][0]

    merged: list[Part] = [parts[0]]
    for part in parts[1:]:
        prev = merged[-1]
        if part[0] == prev[0] and part[1].t - prev[2].t < m["min_gap_s"]:
            prev[2] = max(prev[2], part[2], key=lambda x: x.t)
        else:
            merged.append(part)
    if len(merged) == 1:
        return None, "the beats fit one continuous window"
    parts = merged

    cliff = beats[-1]

    def setup_before_cliff() -> bool:
        lp = parts[-1]
        floor = parts[-2][2].t + m["min_gap_s"] if parts[-2][0] == lp[0] else 0.0
        cand = None if bridge else _strictly_before(cuts[lp[0]], lp[1].t)
        if cand is not None and cand.t >= floor - _EPS:
            lp[1] = cand
            return True
        return False

    def after_hook() -> bool:
        fp = parts[0]
        ceiling = parts[1][1].t - m["min_gap_s"] if parts[1][0] == fp[0] else eps[fp[0]]["duration_s"]
        cand = None if bridge else _strictly_after(cuts[fp[0]], fp[2].t)
        if cand is not None and cand.t <= ceiling + _EPS:
            fp[2] = cand
            return True
        return False

    def middles() -> bool:
        for i in range(1, len(parts) - 1):
            mp = parts[i]
            ceiling = parts[i + 1][1].t - m["min_gap_s"] if parts[i + 1][0] == mp[0] else eps[mp[0]]["duration_s"]
            cand = _strictly_after(cuts[mp[0]], mp[2].t)
            if cand is not None and cand.t <= ceiling + _EPS:
                mp[2] = cand
                return True
        return False

    def past_cliff() -> bool:
        lp = parts[-1]
        cand = _strictly_after(cuts[lp[0]], lp[2].t)
        if cand is not None and cand.t - cliff["end_s"] <= c["tail_s"] + _EPS:
            lp[2] = cand
            return True
        return False

    # Parts take turns to grow by one cut, so the opening and the ending stay balanced. Running a
    # little past the cliffhanger beat (inside its tail) is the last resort.
    turns = [setup_before_cliff, after_hook, middles]
    turn = 0

    def grow_once() -> bool:
        nonlocal turn
        for k in range(len(turns)):
            grower = turns[(turn + k) % len(turns)]
            if grower():
                turn = (turn + k + 1) % len(turns)
                return True
        return past_cliff()

    total = lambda: sum(pt[2].t - pt[1].t for pt in parts)  # noqa: E731
    while total() < p["min_s"] - _EPS:
        if not grow_once():
            return None, f"cannot reach {p['min_s']:.0f} s without running into another part ({total():.1f} s)"
    why = start_at_questions(parts, eps, cuts, m)
    if why:
        return None, why
    if total() > p["max_s"] + _EPS:
        return None, f"too long: {total():.1f} s"
    return parts, ""


def score_parts(parts: list[Part], idxs: dict[int, _Index], cfg: dict) -> dict | None:
    """Rule scores for joined parts: the hook from the first part's opening, the cliffhanger from the
    last part's ending, the weakest part's clarity minus a cost per cut, time-weighted pacing."""
    p, w, m = cfg["promo"], cfg["score"]["weights"], cfg["multiseg"]
    idx0, cin0 = idxs[parts[0][0]], parts[0][1]
    hook_score, hook_i, onset, skipped = _hook_part(idx0, cin0, p)
    if hook_i is None:
        return None
    per, durs = [], []
    for n, cin, cout in parts:
        idx = idxs[n]
        h = _hook_part(idx, cin, p)
        hi = h[1] if h[1] is not None else min(idx.lo(cin.t), len(idx.blocks) - 1)
        per.append(_evaluate(idx, cin, cout, hi, h[0], h[2], h[3], cfg, detail=True, require_cliff=False))
        durs.append(cout.t - cin.t)
    last = per[-1]
    if last is None:
        return None
    ok = [(r, d) for r, d in zip(per, durs) if r is not None]
    cliff = last["scores"]["cliffhanger"]
    standalone = max(0.0, min(r["scores"]["standalone"] for r, _ in ok) - m["jump_penalty"] * (len(parts) - 1))
    pacing = sum(r["scores"]["pacing"] * d for r, d in ok) / sum(d for _, d in ok)
    length = length_fit(sum(durs), p)
    total = (w["hook"] * hook_score + w["cliffhanger"] * cliff + w["standalone"] * standalone
             + w["pacing"] * pacing + w["length"] * length)
    f = dict(last["features"])
    f |= {
        "hook_onset_s": onset,
        "hook_group": idx0.sigs[hook_i].hook_group,
        "skipped_hook": round(skipped, 2),
        "clean_in": cin0.shot_boundary,
        "speakers": sorted({s for r, _ in ok for s in r["features"]["speakers"]}),
        "speech_ratio": round(sum(r["features"]["speech_ratio"] * d for r, d in ok) / sum(d for _, d in ok), 2),
        "longest_quiet_s": max(r["features"]["longest_quiet_s"] for r, _ in ok),
        "mid_exchange_start": bool(per[0] and per[0]["features"]["mid_exchange_start"]),
        "graphic": any(r["features"]["graphic"] for r, _ in ok),
        "context_debt": round(sum(r["features"]["context_debt"] for r, _ in ok), 2),
        "parts": len(parts),
    }
    return {
        "hook_block": idx0.blocks[hook_i]["block_id"],
        "cliff_block": last["cliff_block"],
        "scores": {k: round(v, 3) for k, v in (("hook", hook_score), ("cliffhanger", cliff), ("standalone", standalone),
                                                ("pacing", pacing), ("length", length), ("total", total))},
        "features": f,
    }


def make_candidate(parts: list[Part], idxs: dict[int, _Index], cfg: dict, candidate_id: str, kind: str,
                   source: str, hook_block: str | None = None, cliff_block: str | None = None) -> Candidate | None:
    r = score_parts(parts, idxs, cfg)
    if r is None:
        return None
    return Candidate(
        candidate_id=candidate_id,
        kind=kind,
        segments=[Segment(n, cin.t, cout.t) for n, cin, cout in parts],
        hook_block=hook_block or r["hook_block"],
        cliff_block=cliff_block or r["cliff_block"],
        rule_scores=r["scores"],
        features=r["features"],
        source=source,
    )


def _strong(idx: _Index, attr: str, threshold: float, t0: float = 0.0, t1: float = 1e9, limit: int = 6) -> list[int]:
    values = getattr(idx, attr)
    found = [i for i in range(len(idx.blocks)) if values[i] >= threshold and t0 - _EPS <= idx.starts[i] <= t1 + _EPS]
    return sorted(found, key=lambda i: (-values[i], idx.starts[i]))[:limit]


def _same_promo(a: Candidate, b: Candidate, limit: float) -> bool:
    return overlap(a.segments[0], b.segments[0]) > limit and overlap(a.segments[-1], b.segments[-1]) > limit


def tightened_candidates(ep: dict, sig: dict[str, BlockSignals], cuts: list[CutPoint], lex: Lexicon, cfg: dict,
                         singles: list[Candidate]) -> list[Candidate]:
    """Best two-part cuts of one episode: a strong opening, then a strong ending at least
    `min_cut_s` later, with what lies between cut out."""
    m, n = cfg["multiseg"], ep["episode_number"]
    idx = _Index(ep, sig, lex)
    idxs, eps, cut_map = {n: idx}, {n: ep}, {n: cuts}
    options = []
    for h in _strong(idx, "hook", m["min_hook"]):
        for c in _strong(idx, "cliff", m["min_cliff"]):
            if idx.starts[c] - idx.starts[h] < m["min_cut_s"]:
                continue
            parts, _ = build_parts(eps, cut_map, [idx.blocks[h]["block_id"], idx.blocks[c]["block_id"]], cfg)
            if parts is None:
                continue
            cand = make_candidate(parts, idxs, cfg, "", "tightened", "rules")
            if cand is not None:
                options.append(cand)
    options.sort(key=lambda x: (-x.rule_scores["total"], x.segments[0].start_s, x.segments[-1].end_s))
    kept: list[Candidate] = []
    for cand in options:
        if len(kept) >= m["tightened_per_episode"]:
            break
        if any(_same_promo(cand, other, cfg["rank"]["max_overlap"]) for other in singles + kept):
            continue
        cand.candidate_id = f"E{n:03d}-T{len(kept) + 1:02d}"
        kept.append(cand)
    return kept


def is_direct_link(prev: dict, ep: dict, cfg: dict) -> bool:
    """Does ep pick up exactly where prev stopped? Read from the continuity note, so a show without
    notes simply gets no bridges."""
    h = ep["header"]
    return (h["follows_episode"] == prev["episode_number"]
            and bool(re.search(cfg["multiseg"]["direct_link"], h["continuity_notes"] or "", re.I)))


def bridge_candidates(prev: dict, ep: dict, sigs: dict[str, BlockSignals], cuts: dict[int, list[CutPoint]],
                      lex: Lexicon, cfg: dict) -> list[Candidate]:
    """The end of `prev` straight into the start of `ep`: open on a strong beat in prev's last
    `bridge_window_s`, end on a strong beat in ep's first `bridge_window_s`."""
    m = cfg["multiseg"]
    a, b = prev["episode_number"], ep["episode_number"]
    idxs = {a: _Index(prev, sigs, lex), b: _Index(ep, sigs, lex)}
    eps = {a: prev, b: ep}
    options = []
    for h in _strong(idxs[a], "hook", m["min_hook"], t0=prev["duration_s"] - m["bridge_window_s"]):
        for c in _strong(idxs[b], "cliff", m["min_cliff"], t1=m["bridge_window_s"]):
            ids = [idxs[a].blocks[h]["block_id"], idxs[b].blocks[c]["block_id"]]
            parts, _ = build_parts(eps, cuts, ids, cfg, bridge=True)
            if parts is None:
                continue
            cand = make_candidate(parts, idxs, cfg, f"E{b:03d}-X01", "bridge", "rules")
            if cand is not None:
                options.append(cand)
    options.sort(key=lambda x: (-x.rule_scores["total"], x.segments[0].start_s))
    return options[:1]
